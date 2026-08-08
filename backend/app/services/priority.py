"""Priority queue ordering and ageing (TRD §4.3).

    priority = 0.70 * severity + 0.15 * authenticity + 0.15 * ageing_bonus
    ageing_bonus = min(100, minutes_waiting_since_client_created_at * 1.5)

**Ageing uses the client clock, not receipt.** A report filed offline an hour ago has
been waiting an hour, whatever time it managed to reach the server (FR-28). Using
receipt time would punish exactly the people with the worst connectivity.

Ageing is also the starvation guarantee (FR-17): a minor report left alone keeps
climbing, so nothing sits at the bottom of the queue for ever.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.config import settings
from app.core.logging import get_logger
from app.core.time import minutes_between, utcnow
from app.models import Report, ReportStatus

logger = get_logger(__name__)

# A report is in the queue while it is waiting to be dispatched. `verified` is the
# moment before it joins; `queued` is once it has.
QUEUE_STATUSES = (ReportStatus.VERIFIED, ReportStatus.QUEUED)

# Sort bands. Operator intent outranks arithmetic in both directions (FR-18).
BAND_PINNED = 0
BAND_COMPUTED = 1
BAND_DEMOTED = 2


@dataclass(frozen=True)
class PriorityBreakdown:
    """The score and the three numbers it came from, so a queue position is arguable."""

    severity: int
    authenticity: int
    ageing_bonus: float
    minutes_waiting: float
    score: float

    def as_dict(self) -> dict[str, float]:
        return {
            "severity": self.severity,
            "authenticity": self.authenticity,
            "ageing_bonus": round(self.ageing_bonus, 2),
            "minutes_waiting": round(self.minutes_waiting, 1),
            "score": self.score,
        }


def ageing_bonus(client_created_at: datetime, now: datetime | None = None) -> float:
    """Wait-time bonus, capped. Negative waits (clock skew) contribute nothing."""
    waited = minutes_between(client_created_at, now or utcnow())
    return min(settings.ageing_max_bonus, max(0.0, waited) * settings.ageing_rate_per_minute)


def compute_priority(report: Report, now: datetime | None = None) -> PriorityBreakdown:
    """Score one report. Unscored inputs count as zero rather than failing."""
    moment = now or utcnow()
    severity = report.severity_score or 0
    authenticity = report.authenticity_score or 0
    waited = max(0.0, minutes_between(report.client_created_at, moment))
    bonus = ageing_bonus(report.client_created_at, moment)

    score = (
        settings.priority_weight_severity * severity
        + settings.priority_weight_authenticity * authenticity
        + settings.priority_weight_ageing * bonus
    )

    return PriorityBreakdown(
        severity=severity,
        authenticity=authenticity,
        ageing_bonus=bonus,
        minutes_waiting=waited,
        score=round(score, 2),
    )


def band_of(report: Report) -> int:
    """Which sort band a report belongs to, from its override rank.

    The data model carries a single nullable integer for operator intent, so the sign
    encodes direction: positive pins to the top, negative demotes to the bottom, null
    leaves the computed score in charge.
    """
    if report.manual_override_rank is None:
        return BAND_COMPUTED
    return BAND_PINNED if report.manual_override_rank >= 0 else BAND_DEMOTED


def sort_key(report: Report, breakdown: PriorityBreakdown) -> tuple:
    """Ordering: pinned first, then computed score, then demoted.

    Ties break on client timestamp then id, so the queue is stable and reproducible
    rather than dependent on however the database returned the rows.
    """
    band = band_of(report)
    rank = report.manual_override_rank

    if band == BAND_PINNED:
        within = float(rank or 0)
    elif band == BAND_DEMOTED:
        within = float(-(rank or 0))  # −1 sits above −2
    else:
        within = 0.0

    return (band, within, -breakdown.score, report.client_created_at, str(report.id))


@dataclass(frozen=True)
class QueueEntry:
    position: int
    report: Report
    breakdown: PriorityBreakdown


def build_queue(session: Session, now: datetime | None = None) -> list[QueueEntry]:
    """The ordered queue of reports awaiting dispatch.

    Scoring happens in Python rather than SQL because the ageing term depends on the
    current time, so it cannot be indexed usefully. At demo scale the queue is tens of
    rows; a production deployment would materialise the score periodically instead.
    """
    moment = now or utcnow()

    reports = list(
        session.exec(select(Report).where(Report.status.in_(QUEUE_STATUSES))).all()  # type: ignore[union-attr]
    )

    scored = [(report, compute_priority(report, moment)) for report in reports]
    scored.sort(key=lambda pair: sort_key(pair[0], pair[1]))

    return [
        QueueEntry(position=index, report=report, breakdown=breakdown)
        for index, (report, breakdown) in enumerate(scored, start=1)
    ]


def refresh_priorities(session: Session, entries: list[QueueEntry]) -> None:
    """Persist the freshly computed scores (TRD §3: "refreshed on read").

    The stored column is a snapshot for reference and for Phase 7's dispatch; the
    queue endpoint always recomputes, because ageing moves with the clock.
    """
    changed = False
    for entry in entries:
        if entry.report.priority_score != entry.breakdown.score:
            entry.report.priority_score = entry.breakdown.score
            session.add(entry.report)
            changed = True

    if changed:
        session.commit()


def enqueue_verified(session: Session, report: Report, now: datetime | None = None) -> bool:
    """Move a verified report into the queue. Returns True if it moved."""
    if report.status != ReportStatus.VERIFIED:
        return False

    report.status = ReportStatus.QUEUED
    report.priority_score = compute_priority(report, now).score
    session.add(report)
    return True


def enqueue_pending(limit: int | None = None) -> int:
    """Sweep every verified report into the queue."""
    from app.db import engine  # local import keeps this module import-light

    with Session(engine) as session:
        statement = select(Report).where(Report.status == ReportStatus.VERIFIED)
        if limit is not None:
            statement = statement.limit(limit)

        moved = 0
        for report in session.exec(statement).all():
            if enqueue_verified(session, report):
                moved += 1
        if moved:
            session.commit()

    if moved:
        logger.info("reports queued", extra={"count": moved})
    return moved


def find_position(session: Session, report_id: uuid.UUID, now: datetime | None = None) -> int | None:
    """Where a report currently sits in the queue, or None if it is not in it."""
    for entry in build_queue(session, now):
        if entry.report.id == report_id:
            return entry.position
    return None
