"""Responder lifecycle: status updates and rejection (FR-21, FR-22)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.errors import ConflictError, ErrorCode, ErrorEnvelope, NotFoundError
from app.core.logging import get_logger
from app.core.time import minutes_between, utcnow
from app.db import get_session
from app.models import (
    LEGAL_TRANSITIONS,
    Assignment,
    Report,
    ReportStatus,
    Responder,
    is_legal_transition,
)
from app.schemas.assignment import (
    AssignmentRead,
    LifecycleResult,
    RejectRequest,
    StatusUpdateRequest,
)
from app.services.dispatch import STATUS_ACTIVITY, advance_assignment, reject_assignment
from app.services.priority import find_position

logger = get_logger(__name__)

router = APIRouter(prefix="/api/assignments", tags=["assignments"])


def _load(session: Session, assignment_id: uuid.UUID) -> tuple[Assignment, Report, Responder]:
    assignment = session.get(Assignment, assignment_id)
    if assignment is None:
        raise NotFoundError(
            "No assignment exists with that id",
            code=ErrorCode.ASSIGNMENT_NOT_FOUND,
            detail={"assignment_id": str(assignment_id)},
        )

    report = session.get(Report, assignment.report_id)
    responder = session.get(Responder, assignment.responder_id)
    if report is None or responder is None:  # pragma: no cover — foreign keys prevent it
        raise NotFoundError(
            "The assignment references a missing report or responder",
            code=ErrorCode.ASSIGNMENT_NOT_FOUND,
            detail={"assignment_id": str(assignment_id)},
        )

    return assignment, report, responder


def _read(assignment: Assignment, report: Report, responder: Responder) -> AssignmentRead:
    payload = AssignmentRead.model_validate(assignment)
    payload.responder_name = responder.name
    payload.report_status = report.status
    payload.is_open = assignment.is_open
    return payload


@router.post(
    "/{assignment_id}/status",
    response_model=LifecycleResult,
    summary="Advance an assignment through its lifecycle",
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope, "description": "Assignment closed, or illegal transition"},
        422: {"model": ErrorEnvelope},
    },
)
def update_status(
    assignment_id: uuid.UUID,
    payload: StatusUpdateRequest,
    session: Session = Depends(get_session),
) -> LifecycleResult:
    """Move a report along `acknowledged → en_route → on_scene → resolved → closed`.

    Only transitions the lifecycle permits are accepted. Skipping a step is refused
    with a typed error naming what *is* allowed from here — a crew that reports "on
    scene" without ever acknowledging has almost certainly hit the wrong button, and
    silently accepting it would corrupt the cycle times Phase 9 mines.
    """
    assignment, report, responder = _load(session, assignment_id)
    previous = report.status

    # A rejected assignment is dead — the report has moved on to another crew. A
    # *resolved* one is not: `closed` is the control room signing off work that is
    # already finished, and it is still the assignment the sign-off belongs to.
    if assignment.rejected_at is not None:
        raise ConflictError(
            "This assignment was rejected and can no longer be updated",
            code=ErrorCode.ASSIGNMENT_CLOSED,
            detail={
                "assignment_id": str(assignment_id),
                "rejected_at": assignment.rejected_at.isoformat(),
                "rejection_reason": assignment.rejection_reason,
            },
        )

    if not is_legal_transition(previous, payload.status):
        raise ConflictError(
            f"A report cannot move from {previous.value} to {payload.status.value}",
            code=ErrorCode.ILLEGAL_TRANSITION,
            detail={
                "report_id": str(report.id),
                "current_status": previous.value,
                "requested_status": payload.status.value,
                "allowed": sorted(status.value for status in LEGAL_TRANSITIONS[previous]),
            },
        )

    if payload.status not in STATUS_ACTIVITY:
        # `queued` is reachable from here in the lifecycle, but only as a rejection —
        # which carries a reason and belongs on its own endpoint.
        raise ConflictError(
            f"{payload.status.value} is not a responder status update",
            code=ErrorCode.ILLEGAL_TRANSITION,
            detail={
                "requested_status": payload.status.value,
                "hint": "use POST /api/assignments/{id}/reject to return a report to the queue",
            },
        )

    advance_assignment(
        session, assignment, report, responder, payload.status, payload.actor, payload.note
    )
    session.commit()
    for row in (assignment, report, responder):
        session.refresh(row)

    return LifecycleResult(
        assignment=_read(assignment, report, responder),
        report_status=report.status,
        previous_status=previous,
        responder_active_count=responder.active_count,
        responder_status=responder.status,
        minutes_waiting=round(minutes_between(report.client_created_at, utcnow()), 1),
    )


@router.post(
    "/{assignment_id}/reject",
    response_model=LifecycleResult,
    summary="Decline an assignment, returning the report to the queue",
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)
def reject(
    assignment_id: uuid.UUID,
    payload: RejectRequest,
    session: Session = Depends(get_session),
) -> LifecycleResult:
    """Hand a report back (FR-21).

    The report rejoins the queue **with the wait time it already accrued** — ageing runs
    from when the citizen filed it, not from when a crew handed it back, so nobody is
    penalised for a responder's unavailability.

    The rejecting responder is not offered the same report again; otherwise it would
    return to the queue, match them once more, and loop.
    """
    assignment, report, responder = _load(session, assignment_id)
    previous = report.status

    if not assignment.is_open:
        raise ConflictError(
            "This assignment is already closed",
            code=ErrorCode.ASSIGNMENT_CLOSED,
            detail={
                "assignment_id": str(assignment_id),
                "resolved_at": assignment.resolved_at.isoformat() if assignment.resolved_at else None,
                "rejected_at": assignment.rejected_at.isoformat() if assignment.rejected_at else None,
            },
        )

    if not is_legal_transition(previous, ReportStatus.QUEUED):
        raise ConflictError(
            f"A report at {previous.value} cannot be returned to the queue",
            code=ErrorCode.ILLEGAL_TRANSITION,
            detail={
                "report_id": str(report.id),
                "current_status": previous.value,
                "allowed": sorted(status.value for status in LEGAL_TRANSITIONS[previous]),
            },
        )

    reject_assignment(session, assignment, report, responder, payload.reason, payload.actor)
    session.commit()
    for row in (assignment, report, responder):
        session.refresh(row)

    return LifecycleResult(
        assignment=_read(assignment, report, responder),
        report_status=report.status,
        previous_status=previous,
        responder_active_count=responder.active_count,
        responder_status=responder.status,
        queue_position=find_position(session, report.id),
        minutes_waiting=round(minutes_between(report.client_created_at, utcnow()), 1),
    )
