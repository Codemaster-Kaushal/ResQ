"""Cycle times and bottleneck detection (FR-24)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.logging import get_logger
from app.db import get_session
from app.schemas.events import BottleneckReport, TransitionRead
from app.services.mining import TransitionStat, analyse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/mining", tags=["mining"])


def _to_read(stat: TransitionStat) -> TransitionRead:
    return TransitionRead(
        transition=stat.transition,
        from_activity=stat.from_activity,
        to_activity=stat.to_activity,
        closed_cases=stat.closed_cases,
        median_minutes=stat.median_minutes,
        open_cases=stat.open_cases,
        current_mean_minutes=stat.current_mean_minutes,
        deviation_ratio=stat.deviation_ratio,
        is_bottleneck=stat.is_bottleneck,
        suggested_action=stat.suggested_action,
    )


@router.get(
    "/bottlenecks",
    response_model=BottleneckReport,
    summary="Where the response process is slowing down",
)
def bottlenecks(session: Session = Depends(get_session)) -> BottleneckReport:
    """Compare what each stage is taking now against what it historically took.

    Medians come from cases that ran to completion; the current figures come from cases
    still in flight. A stage running past `BOTTLENECK_DEVIATION_RATIO` times its median
    is flagged with a suggested action.

    This turns "the response felt slow today" into "reports spent a median of nine
    minutes waiting for dispatch across completed cases, and are averaging ninety-four
    right now" — a claim with a number attached to it.
    """
    report = analyse(session)

    return BottleneckReport(
        generated_at=report.generated_at,
        closed_cases=report.closed_cases,
        open_cases=report.open_cases,
        deviation_threshold=report.deviation_threshold,
        bottlenecks=[_to_read(stat) for stat in report.bottlenecks],
        transitions=[_to_read(stat) for stat in report.transitions],
        note=report.note,
    )
