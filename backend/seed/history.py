"""Backdated process history for the demo dataset.

Bottleneck detection compares what a stage is taking *now* against what it took across
completed cases. With no completed cases there is no baseline, so a freshly seeded
database can only answer "no data" — and Phase 9's acceptance asks for a real finding.

This module lays down the history that question needs:

- every report gets its early-stage events, backdated to when it actually arrived
- a handful of older reports are carried all the way through to ``closed``, with
  plausible durations, so the medians are learned from something

The durations are drawn from a generator seeded on each report's own key, so the same
dataset produces the same history on every machine (NFR-4).

Deliberately *not* touched: the fixtures earlier phases depend on. Closing
``latest-critical`` would empty the top of the queue, and closing
``aged-low-severity`` would remove the report that demonstrates ageing.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.models import Activity, Assignment, ProcessEvent, Report, ReportStatus, Responder
from app.services.events import emit_event

# Reports whose state other phases' acceptance criteria rely on.
PROTECTED_KEYS = frozenset(
    {
        "latest-critical",      # Phase 6: must sit at the top of the queue
        "aged-low-severity",    # Phase 6: must still be waiting, to show ageing
        "filler-03",            # Phase 7: the skill-beats-proximity dispatch case
        "filler-08",            # Phase 5: EXIF match
        "filler-10",            # Phase 5: EXIF mismatch
        "dup-image-a",          # Phase 5: the un-penalised original
        "corroborated-1",
        "corroborated-2",
        "corroborated-3",
    }
)

# How many cases to carry through to closed. Enough for a stable median, few enough
# to leave the queue looking like an active incident.
HISTORICAL_CASES = 8

# The whole lifecycle takes roughly an hour and a half at the slow end, so only
# reports older than this can plausibly have finished by now.
MIN_AGE_MINUTES = 150

# Per-stage duration ranges in minutes: (from_activity, to_activity, low, high).
LIFECYCLE_STAGES: tuple[tuple[Activity, Activity, float, float], ...] = (
    (Activity.QUEUED, Activity.ASSIGNED, 6.0, 18.0),
    (Activity.ASSIGNED, Activity.ACKNOWLEDGED, 1.0, 3.0),
    (Activity.ACKNOWLEDGED, Activity.EN_ROUTE, 2.0, 5.0),
    (Activity.EN_ROUTE, Activity.ON_SCENE, 7.0, 16.0),
    (Activity.ON_SCENE, Activity.RESOLVED, 18.0, 40.0),
    (Activity.RESOLVED, Activity.CLOSED, 3.0, 9.0),
)

# Sub-minute gaps for the automated stages; these are machine steps, not human ones.
INTAKE_OFFSETS_SECONDS = {
    Activity.TRIAGE_COMPLETED: 2.0,
    Activity.AUTHENTICITY_SCORED: 3.2,
    Activity.REPORT_VERIFIED: 3.6,
    Activity.QUEUED: 4.1,
}


@dataclass
class HistorySummary:
    intake_cases: int = 0
    closed_cases: int = 0
    events_written: int = 0


def _rng(report: Report) -> random.Random:
    return random.Random(f"rescuenet-history:{report.idempotency_key}")


def _take_ownership(session: Session, report_ids: set[uuid.UUID]) -> list[Report]:
    """Load the freshly seeded reports and clear the events the pipeline just wrote.

    Seeding runs reports through the real triage → authenticity → queue pipeline, which
    emits events stamped with the moment the seed ran. Those timestamps describe when
    the *seed* executed, not when the incidents happened, and would leave every case
    looking a few milliseconds old. The seed is constructing a synthetic past, so it
    replaces that timeline with a backdated one.

    Only reports created in this run are touched, which keeps a re-run a no-op.
    """
    if not report_ids:
        return []

    for event in session.exec(select(ProcessEvent)).all():
        if event.case_id in report_ids:
            session.delete(event)
    session.flush()

    reports = session.exec(select(Report).order_by(Report.received_at, Report.id)).all()
    return [report for report in reports if report.id in report_ids]


def _write_intake(session: Session, report: Report) -> int:
    """The automated stages every report passes through, timed from its arrival."""
    arrival = report.received_at

    emit_event(
        session,
        case_id=report.id,
        activity=Activity.REPORT_RECEIVED,
        resource=f"reporter:{report.reporter_pseudonym}",
        metadata={"idempotency_key": report.idempotency_key},
        timestamp=arrival,
    )
    written = 1

    emit_event(
        session,
        case_id=report.id,
        activity=Activity.TRIAGE_COMPLETED,
        resource=f"scorer:{report.scoring_provider or 'local'}",
        metadata={
            "incident_type": report.incident_type.value if report.incident_type else None,
            "severity_score": report.severity_score,
        },
        timestamp=arrival + timedelta(seconds=INTAKE_OFFSETS_SECONDS[Activity.TRIAGE_COMPLETED]),
    )
    written += 1

    emit_event(
        session,
        case_id=report.id,
        activity=Activity.AUTHENTICITY_SCORED,
        metadata={"authenticity_score": report.authenticity_score},
        timestamp=arrival
        + timedelta(seconds=INTAKE_OFFSETS_SECONDS[Activity.AUTHENTICITY_SCORED]),
    )
    written += 1

    flagged = report.status == ReportStatus.FLAGGED
    emit_event(
        session,
        case_id=report.id,
        activity=Activity.REPORT_FLAGGED if flagged else Activity.REPORT_VERIFIED,
        metadata={"authenticity_score": report.authenticity_score},
        timestamp=arrival + timedelta(seconds=INTAKE_OFFSETS_SECONDS[Activity.REPORT_VERIFIED]),
    )
    written += 1

    if not flagged:
        emit_event(
            session,
            case_id=report.id,
            activity=Activity.QUEUED,
            metadata={"priority_score": report.priority_score},
            timestamp=arrival + timedelta(seconds=INTAKE_OFFSETS_SECONDS[Activity.QUEUED]),
        )
        written += 1

    return written


def _queued_at(report: Report) -> datetime:
    return report.received_at + timedelta(seconds=INTAKE_OFFSETS_SECONDS[Activity.QUEUED])


def _eligible_for_closure(reports: list[Report], now: datetime) -> list[Report]:
    """Old enough to have plausibly finished, and not needed by another phase."""
    candidates = [
        report
        for report in reports
        if report.status == ReportStatus.QUEUED
        and report.idempotency_key.removeprefix("seed-") not in PROTECTED_KEYS
        and (now - report.received_at) > timedelta(minutes=MIN_AGE_MINUTES)
    ]
    # Oldest first, so the finished cases are the ones that have had the most time.
    candidates.sort(key=lambda report: report.received_at)
    return candidates[:HISTORICAL_CASES]


def _close_case(
    session: Session, report: Report, responder: Responder, now: datetime
) -> int:
    """Walk one report from `queued` to `closed`, backdated and self-consistent."""
    rng = _rng(report)
    moment = _queued_at(report)
    written = 0

    assigned_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    for _, to_activity, low, high in LIFECYCLE_STAGES:
        moment = moment + timedelta(minutes=round(rng.uniform(low, high), 2))
        if moment >= now:
            # Ran out of runway; leave the case where it is rather than fabricate a
            # future event.
            break

        if to_activity == Activity.ASSIGNED:
            assigned_at = moment
        elif to_activity == Activity.ACKNOWLEDGED:
            acknowledged_at = moment
        elif to_activity == Activity.RESOLVED:
            resolved_at = moment

        emit_event(
            session,
            case_id=report.id,
            activity=to_activity,
            resource=(
                "operator:controller-meera"
                if to_activity in (Activity.ASSIGNED, Activity.CLOSED)
                else f"responder:{responder.name}"
            ),
            metadata={"responder_name": responder.name},
            timestamp=moment,
        )
        written += 1
        report.status = ReportStatus(to_activity.value.lower())

    if assigned_at is not None:
        session.add(
            Assignment(
                report_id=report.id,
                responder_id=responder.id,
                assigned_at=assigned_at,
                acknowledged_at=acknowledged_at,
                # Set, so the assignment is closed and holds none of the crew's capacity.
                resolved_at=resolved_at,
            )
        )

    session.add(report)
    return written


def replay(
    session: Session,
    now: datetime,
    responders: list[Responder],
    report_ids: set[uuid.UUID],
) -> HistorySummary:
    """Give the dataset a past. A no-op when the seed created nothing new."""
    summary = HistorySummary()

    fresh = _take_ownership(session, report_ids)
    if not fresh:
        return summary

    for report in fresh:
        summary.events_written += _write_intake(session, report)
        summary.intake_cases += 1

    if responders:
        for index, report in enumerate(_eligible_for_closure(fresh, now)):
            responder = responders[index % len(responders)]
            summary.events_written += _close_case(session, report, responder, now)
            summary.closed_cases += 1

    session.commit()
    return summary
