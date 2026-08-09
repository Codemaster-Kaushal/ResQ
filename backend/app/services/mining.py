"""Cycle times and bottleneck detection (TRD §4.5).

    For each activity transition pair, compute the median duration across all closed
    cases. Flag any transition where the current open cases' mean wait exceeds
    1.5 x the median.

This is the difference between "response felt slow today" and "reports spent a median
of 12 minutes waiting for dispatch last week and are averaging 94 minutes right now".
The event log is the only place that question can be answered from, which is why it is
append-only.

**One judgement the spec leaves open.** A closed case gives a complete pair —
`QUEUED → ASSIGNED` took nine minutes. An open case does not: a report sitting at
`QUEUED` has no successor yet, so there is no pair to attribute its wait to. Waits are
therefore grouped by the activity a case is *stuck at*, and compared against the median
of the transition that most often follows it. That is the comparison an operator
actually wants: "cases at this stage are taking longer than cases at this stage
normally take".
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

from sqlmodel import Session, select

from app.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import Activity, ProcessEvent

logger = get_logger(__name__)

# A case is finished once it reaches one of these; nothing follows, so its trail is
# complete and its durations are safe to learn from.
TERMINAL_ACTIVITIES = frozenset({Activity.CLOSED.value, Activity.REPORT_REJECTED.value})

# What to do about a slow stage. Generic enough to be honest, specific enough to act on.
SUGGESTED_ACTIONS: dict[str, str] = {
    Activity.REPORT_RECEIVED.value: (
        "Reports are waiting on triage. Check the scoring provider chain — a timing out "
        "provider delays classification even though it never blocks ingestion."
    ),
    Activity.TRIAGE_COMPLETED.value: (
        "Authenticity scoring is lagging behind triage. Check for a slow duplicate-image "
        "lookup as the report volume grows."
    ),
    Activity.AUTHENTICITY_SCORED.value: (
        "Scored reports are not being routed. Check the verification step."
    ),
    Activity.REPORT_FLAGGED.value: (
        "Flagged reports are waiting on human review. Add a reviewer to the control room, "
        "or re-check the authenticity threshold if the queue is full of false positives."
    ),
    Activity.REPORT_VERIFIED.value: (
        "Verified reports are not reaching the queue. Check the queueing step."
    ),
    Activity.QUEUED.value: (
        "Reports are waiting too long for a responder. Add crews, widen "
        "DISPATCH_MAX_RADIUS_KM, or check whether units are stuck at capacity."
    ),
    Activity.ASSIGNED.value: (
        "Responders are slow to acknowledge. Check that assignment notifications are "
        "reaching crews in the field."
    ),
    Activity.ACKNOWLEDGED.value: (
        "Crews are acknowledging but not departing. Check for vehicle or staffing "
        "shortages at the responding units."
    ),
    Activity.EN_ROUTE.value: (
        "Travel times are elevated. Check for blocked routes and consider repositioning "
        "units closer to the active zones."
    ),
    Activity.ON_SCENE.value: (
        "On-scene work is taking longer than usual. Consider dispatching support to the "
        "affected incidents."
    ),
    Activity.RESOLVED.value: (
        "Resolved cases are not being closed out. This is administrative, not "
        "operational — but it distorts every cycle-time measure until it is cleared."
    ),
}

GENERIC_ACTION = "Investigate this stage; it is running well above its historical median."


@dataclass
class TransitionStat:
    """One stage of the process, measured."""

    transition: str
    from_activity: str
    to_activity: str | None
    closed_cases: int
    median_minutes: float
    open_cases: int
    current_mean_minutes: float
    deviation_ratio: float
    is_bottleneck: bool
    suggested_action: str | None = None


@dataclass
class MiningReport:
    generated_at: datetime
    closed_cases: int
    open_cases: int
    deviation_threshold: float
    transitions: list[TransitionStat] = field(default_factory=list)
    bottlenecks: list[TransitionStat] = field(default_factory=list)
    note: str | None = None


def _trails(session: Session) -> dict[uuid.UUID, list[ProcessEvent]]:
    """Every case's events, in order. Ties break on id so the order is deterministic."""
    events = session.exec(
        select(ProcessEvent).order_by(ProcessEvent.timestamp, ProcessEvent.id)
    ).all()

    trails: dict[uuid.UUID, list[ProcessEvent]] = defaultdict(list)
    for event in events:
        trails[event.case_id].append(event)
    return trails


def _is_closed(trail: list[ProcessEvent]) -> bool:
    return bool(trail) and trail[-1].activity in TERMINAL_ACTIVITIES


def minutes(earlier: datetime, later: datetime) -> float:
    return max(0.0, (later - earlier).total_seconds() / 60)


def analyse(session: Session, now: datetime | None = None) -> MiningReport:
    """Compute per-transition cycle times and flag the slow stages (FR-24)."""
    moment = now or utcnow()
    trails = _trails(session)

    closed = {case: trail for case, trail in trails.items() if _is_closed(trail)}
    open_cases = {case: trail for case, trail in trails.items() if not _is_closed(trail)}

    # Observed durations, keyed by the directly-follows pair.
    durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    for trail in closed.values():
        for earlier, later in zip(trail, trail[1:]):
            durations[(earlier.activity, later.activity)].append(
                minutes(earlier.timestamp, later.timestamp)
            )

    # How long each open case has been sitting where it is.
    waits: dict[str, list[float]] = defaultdict(list)
    for trail in open_cases.values():
        if trail:
            waits[trail[-1].activity].append(minutes(trail[-1].timestamp, moment))

    # Collapse pairs onto the activity they start from, keeping the most common
    # successor as the transition's name.
    by_source: dict[str, list[float]] = defaultdict(list)
    successors: dict[str, Counter] = defaultdict(Counter)
    for (source, target), samples in durations.items():
        by_source[source].extend(samples)
        successors[source][target] += len(samples)

    stats: list[TransitionStat] = []
    for source in sorted(set(by_source) | set(waits)):
        samples = by_source.get(source, [])
        pending = waits.get(source, [])

        median_minutes = round(median(samples), 2) if samples else 0.0
        current_mean = round(sum(pending) / len(pending), 2) if pending else 0.0

        target = successors[source].most_common(1)[0][0] if successors[source] else None
        # Ratio is only meaningful against a real historical baseline.
        ratio = round(current_mean / median_minutes, 2) if median_minutes > 0 else 0.0
        flagged = bool(samples) and bool(pending) and ratio > settings.bottleneck_deviation_ratio

        stats.append(
            TransitionStat(
                transition=f"{source}→{target}" if target else source,
                from_activity=source,
                to_activity=target,
                closed_cases=len(samples),
                median_minutes=median_minutes,
                open_cases=len(pending),
                current_mean_minutes=current_mean,
                deviation_ratio=ratio,
                is_bottleneck=flagged,
                suggested_action=(
                    SUGGESTED_ACTIONS.get(source, GENERIC_ACTION) if flagged else None
                ),
            )
        )

    bottlenecks = sorted(
        (stat for stat in stats if stat.is_bottleneck),
        key=lambda stat: stat.deviation_ratio,
        reverse=True,
    )

    note = None
    if not closed:
        note = (
            "No closed cases yet, so there is no historical baseline to compare against. "
            "Take a report through to `closed` and the medians appear."
        )

    logger.info(
        "bottleneck analysis complete",
        extra={
            "closed_cases": len(closed),
            "open_cases": len(open_cases),
            "bottlenecks": [stat.transition for stat in bottlenecks],
        },
    )

    return MiningReport(
        generated_at=moment,
        closed_cases=len(closed),
        open_cases=len(open_cases),
        deviation_threshold=settings.bottleneck_deviation_ratio,
        transitions=stats,
        bottlenecks=bottlenecks,
        note=note,
    )
