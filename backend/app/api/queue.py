"""The priority queue: severity-ordered dispatch, and the operator's right to override it."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.errors import ConflictError, ErrorCode, ErrorEnvelope, NotFoundError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db import get_session
from app.models import Activity, Report
from app.schemas.queue import (
    OverrideAction,
    OverrideRequest,
    OverrideResult,
    PriorityBreakdownRead,
    QueueItem,
    QueuePage,
)
from app.services.events import emit_event
from app.services.priority import (
    QUEUE_STATUSES,
    QueueEntry,
    band_of,
    build_queue,
    find_position,
    refresh_priorities,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/queue", tags=["queue"])

MAX_PAGE_SIZE = 200
DEFAULT_PIN_RANK = 0
DEFAULT_DEMOTE_RANK = 1


def _to_item(entry: QueueEntry) -> QueueItem:
    report = entry.report
    band = band_of(report)
    return QueueItem(
        position=entry.position,
        id=report.id,
        idempotency_key=report.idempotency_key,
        text=report.text,
        incident_type=report.incident_type,
        status=report.status,
        lat=report.lat,
        lng=report.lng,
        client_created_at=report.client_created_at,
        received_at=report.received_at,
        priority_score=entry.breakdown.score,
        priority=PriorityBreakdownRead(**entry.breakdown.as_dict()),
        manual_override_rank=report.manual_override_rank,
        pinned=band == 0,
        demoted=band == 2,
    )


@router.get(
    "",
    response_model=QueuePage,
    summary="The ordered priority queue",
)
def read_queue(
    session: Session = Depends(get_session),
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QueuePage:
    """Reports awaiting dispatch, worst first.

    The order is recomputed on every read because the ageing term moves with the clock
    (TRD §3, §4.3) — a queue cached even for a minute would start lying about who has
    been waiting longest. Each entry carries the three components behind its score.
    """
    generated_at = utcnow()
    entries = build_queue(session, generated_at)
    refresh_priorities(session, entries)

    window = entries[offset : offset + limit]

    return QueuePage(
        items=[_to_item(entry) for entry in window],
        total=len(entries),
        limit=limit,
        offset=offset,
        generated_at=generated_at,
    )


@router.post(
    "/{report_id}/override",
    response_model=OverrideResult,
    summary="Pin, demote, or clear an operator override",
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope, "description": "Report is not in the queue"},
        422: {"model": ErrorEnvelope},
    },
)
def override_position(
    report_id: uuid.UUID,
    payload: OverrideRequest,
    session: Session = Depends(get_session),
) -> OverrideResult:
    """Move a report by hand, above or below what the score says (FR-18).

    Operator judgement outranks the arithmetic — they have context the model does not.
    Every override is recorded as a process event carrying who did it and what the
    position was before, so the human-in-the-loop claim is evidenced in the log rather
    than asserted in the pitch (FR-30).
    """
    report = session.get(Report, report_id)
    if report is None:
        raise NotFoundError(
            "No report exists with that id",
            code=ErrorCode.REPORT_NOT_FOUND,
            detail={"report_id": str(report_id)},
        )

    if report.status not in QUEUE_STATUSES:
        raise ConflictError(
            "Only a report in the queue can be reordered",
            code=ErrorCode.REPORT_NOT_IN_QUEUE,
            detail={
                "report_id": str(report_id),
                "status": report.status.value,
                "queueable": [status.value for status in QUEUE_STATUSES],
            },
        )

    now = utcnow()
    previous_rank = report.manual_override_rank
    previous_position = find_position(session, report_id, now)

    if payload.action == OverrideAction.PIN:
        report.manual_override_rank = payload.rank if payload.rank is not None else DEFAULT_PIN_RANK
    elif payload.action == OverrideAction.DEMOTE:
        # Negative ranks encode the demoted band; the request supplies magnitude only.
        magnitude = payload.rank if payload.rank is not None else DEFAULT_DEMOTE_RANK
        report.manual_override_rank = -max(1, magnitude)
    else:
        report.manual_override_rank = None

    session.add(report)

    event = emit_event(
        session,
        case_id=report.id,
        activity=Activity.PRIORITY_OVERRIDDEN,
        resource=f"operator:{payload.operator}",
        metadata={
            "action": payload.action.value,
            "rank": report.manual_override_rank,
            "previous_rank": previous_rank,
            "previous_position": previous_position,
            "reason": payload.reason,
        },
    )

    session.commit()
    session.refresh(report)
    session.refresh(event)

    new_position = find_position(session, report_id, now)

    logger.info(
        "queue position overridden",
        extra={
            "report_id": str(report_id),
            "operator": payload.operator,
            "action": payload.action.value,
            "from_position": previous_position,
            "to_position": new_position,
        },
    )

    return OverrideResult(
        id=report.id,
        action=payload.action,
        operator=payload.operator,
        reason=payload.reason,
        manual_override_rank=report.manual_override_rank,
        previous_rank=previous_rank,
        previous_position=previous_position,
        position=new_position,
        event_id=event.id,
    )
