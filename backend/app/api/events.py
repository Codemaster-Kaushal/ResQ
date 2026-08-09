"""The process event log and its process-mining export (FR-23, FR-25)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Annotated, Any, Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db import engine, get_session
from app.models import ProcessEvent
from app.schemas.events import EventPage, EventRead

logger = get_logger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])

MAX_PAGE_SIZE = 500
CSV_BATCH = 500

# Process-mining convention. Disco, ProM and pm4py all read this shape without mapping.
CSV_COLUMNS = ("case_id", "activity", "timestamp", "resource")


def _filters(
    case_id: uuid.UUID | None,
    activity: str | None,
    resource: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list[Any]:
    conditions: list[Any] = []
    if case_id is not None:
        conditions.append(ProcessEvent.case_id == case_id)
    if activity is not None:
        conditions.append(ProcessEvent.activity == activity)
    if resource is not None:
        conditions.append(ProcessEvent.resource == resource)
    if since is not None:
        conditions.append(ProcessEvent.timestamp >= since)
    if until is not None:
        conditions.append(ProcessEvent.timestamp <= until)
    return conditions


@router.get("", response_model=EventPage, summary="The process event log")
def list_events(
    session: Session = Depends(get_session),
    case_id: Annotated[uuid.UUID | None, Query(description="Filter to one report")] = None,
    activity: Annotated[str | None, Query(max_length=64)] = None,
    resource: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EventPage:
    """Append-only, oldest first — the order a case actually happened in.

    Filtering by `case_id` replays one report end to end, which is the view an operator
    asks for when they want to know why something took as long as it did.
    """
    conditions = _filters(case_id, activity, resource, since, until)

    total = session.exec(
        select(func.count()).select_from(ProcessEvent).where(*conditions)
    ).one()

    rows = session.exec(
        select(ProcessEvent)
        .where(*conditions)
        .order_by(ProcessEvent.timestamp, ProcessEvent.id)
        .limit(limit)
        .offset(offset)
    ).all()

    return EventPage(
        items=[EventRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _csv_rows(conditions: list[Any]) -> Iterator[str]:
    """Stream the log so an export never has to fit in memory all at once."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    def flush() -> str:
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    writer.writerow(CSV_COLUMNS)
    yield flush()

    # Its own session: the response outlives the request-scoped dependency.
    with Session(engine) as session:
        offset = 0
        while True:
            batch = session.exec(
                select(ProcessEvent)
                .where(*conditions)
                .order_by(ProcessEvent.timestamp, ProcessEvent.id)
                .limit(CSV_BATCH)
                .offset(offset)
            ).all()
            if not batch:
                break

            for event in batch:
                writer.writerow(
                    [
                        str(event.case_id),
                        event.activity,
                        event.timestamp.isoformat(timespec="milliseconds"),
                        event.resource,
                    ]
                )
            yield flush()
            offset += CSV_BATCH


@router.get(
    "/export.csv",
    summary="Process-mining CSV export",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "case_id, activity, timestamp, resource"}},
)
def export_csv(
    case_id: Annotated[uuid.UUID | None, Query()] = None,
    activity: Annotated[str | None, Query(max_length=64)] = None,
    resource: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> StreamingResponse:
    """The event log in the four columns process-mining tools expect (FR-25).

    Exactly `case_id, activity, timestamp, resource` and nothing else — Disco, ProM and
    pm4py read this shape with no column mapping. Metadata is deliberately left out;
    it is available from `GET /api/events` when a human wants the detail.
    """
    filename = f"rescuenet-eventlog-{utcnow():%Y%m%d-%H%M%S}.csv"

    return StreamingResponse(
        _csv_rows(_filters(case_id, activity, resource, since, until)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
