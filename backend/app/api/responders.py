"""Responder roster with live load."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.db import get_session
from app.models import Assignment, Responder, ResponderSkill, ResponderStatus
from app.schemas.assignment import ResponderPage, ResponderRead

logger = get_logger(__name__)

router = APIRouter(prefix="/api/responders", tags=["responders"])

MAX_PAGE_SIZE = 200


def _open_counts(session: Session) -> dict:
    """Open assignments per responder, counted from the assignment table itself."""
    counts: dict = {}
    for assignment in session.exec(select(Assignment)).all():
        if assignment.is_open:
            counts[assignment.responder_id] = counts.get(assignment.responder_id, 0) + 1
    return counts


@router.get(
    "",
    response_model=ResponderPage,
    summary="Responders with live load and availability",
)
def list_responders(
    session: Session = Depends(get_session),
    responder_status: Annotated[
        ResponderStatus | None, Query(alias="status", description="Filter by availability")
    ] = None,
    skill: Annotated[ResponderSkill | None, Query()] = None,
    dispatchable: Annotated[
        bool | None, Query(description="Only units that could take a job right now")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponderPage:
    """The roster, with how loaded each unit is.

    ``active_count`` is what dispatch enforces capacity against. ``open_assignments``
    is counted live from the assignment table. The two can differ legitimately: the
    seeded fleet starts with prior workload that predates this system tracking it.
    """
    conditions: list[Any] = []
    if responder_status is not None:
        conditions.append(Responder.status == responder_status)
    if skill is not None:
        conditions.append(Responder.skill == skill)

    total = session.exec(select(func.count()).select_from(Responder).where(*conditions)).one()

    rows = session.exec(
        select(Responder)
        .where(*conditions)
        .order_by(Responder.name)
        .limit(limit)
        .offset(offset)
    ).all()

    counts = _open_counts(session)

    items = []
    for responder in rows:
        if dispatchable is not None and responder.is_dispatchable != dispatchable:
            continue
        payload = ResponderRead.model_validate(responder)
        payload.spare_capacity = max(0, responder.capacity - responder.active_count)
        payload.dispatchable = responder.is_dispatchable
        payload.open_assignments = counts.get(responder.id, 0)
        items.append(payload)

    # `dispatchable` is a derived property, so it is filtered after loading; the total
    # then reflects what the caller actually received.
    if dispatchable is not None:
        total = len(items)

    return ResponderPage(items=items, total=total, limit=limit, offset=offset)
