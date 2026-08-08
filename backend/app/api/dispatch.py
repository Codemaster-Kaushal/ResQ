"""Dispatch: matching the queue to responders (FR-19, FR-20)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.errors import ConflictError, ErrorCode, ErrorEnvelope, NotFoundError
from app.core.logging import get_logger
from app.db import get_session
from app.models import Report
from app.schemas.dispatch import (
    AssignedReport,
    AssignedResponder,
    DispatchRequest,
    DispatchResponse,
    MatchBreakdownRead,
)
from app.services.dispatch import DispatchOutcome, DispatchResult, assign_report, dispatch_next
from app.services.priority import QUEUE_STATUSES

logger = get_logger(__name__)

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])


def _to_response(result: DispatchResult) -> DispatchResponse:
    return DispatchResponse(
        outcome=result.outcome,
        assignment_id=result.assignment.id if result.assignment else None,
        assigned_at=result.assignment.assigned_at if result.assignment else None,
        report=AssignedReport.model_validate(result.report) if result.report else None,
        responder=(
            AssignedResponder.model_validate(result.responder) if result.responder else None
        ),
        match=MatchBreakdownRead(**result.match.as_dict()) if result.match else None,
        candidates_considered=result.candidates_considered,
        reason=result.reason,
    )


@router.post(
    "/assign",
    response_model=DispatchResponse,
    summary="Assign the top of the queue, or a specific report",
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)
def assign(
    payload: DispatchRequest | None = None,
    session: Session = Depends(get_session),
) -> DispatchResponse:
    """Match a report to the best-fit responder — not the first free one.

    Three outcomes, all of them successful HTTP responses because none of them is a
    client error:

    - ``assigned`` — a responder was matched and the report moved to `assigned`
    - ``deferred`` — nobody available in range had spare capacity, so the report
      **stays in the queue** with its accrued wait time intact and a
      `DISPATCH_DEFERRED` event is recorded (TRD §4.4)
    - ``queue_empty`` — there was nothing waiting
    """
    request = payload or DispatchRequest()

    if request.report_id is not None:
        report = session.get(Report, request.report_id)
        if report is None:
            raise NotFoundError(
                "No report exists with that id",
                code=ErrorCode.REPORT_NOT_FOUND,
                detail={"report_id": str(request.report_id)},
            )
        if report.status not in QUEUE_STATUSES:
            raise ConflictError(
                "Only a report waiting in the queue can be assigned",
                code=ErrorCode.REPORT_NOT_IN_QUEUE,
                detail={"report_id": str(report.id), "status": report.status.value},
            )
        result = assign_report(session, report, request.operator)
    else:
        result = dispatch_next(session, request.operator)

    if result.outcome != DispatchOutcome.QUEUE_EMPTY:
        session.commit()
        if result.report is not None:
            session.refresh(result.report)
        if result.responder is not None:
            session.refresh(result.responder)
        if result.assignment is not None:
            session.refresh(result.assignment)

    return _to_response(result)
