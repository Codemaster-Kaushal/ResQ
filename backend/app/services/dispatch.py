"""Responder matching and assignment (TRD §4.4).

    candidates  = available AND active_count < capacity AND within 25 km
    match_score = 0.5 * (1 - distance/25km)
                + 0.3 * skill_component      # exact 1.0, compatible 0.6, mismatch 0.2
                + 0.2 * (1 - active_count/capacity)

The point of the weighting is that **the best-fit responder is not the first free one**
(G3). Distance matters most, but not enough to send a structural crew to a cardiac
arrest when a medical unit is a few hundred metres further away.

No candidate means the report stays in the queue and a ``DISPATCH_DEFERRED`` event is
emitted. A report is never dropped for want of a responder.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from sqlmodel import Session, select

from app.config import settings
from app.core.geo import haversine_km
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import (
    Activity,
    Assignment,
    IncidentType,
    Report,
    ReportStatus,
    Responder,
    ResponderSkill,
    ResponderStatus,
)
from app.services.events import emit_event
from app.services.priority import build_queue

logger = get_logger(__name__)

# Which skill an incident calls for (TRD §4.4).
SKILL_FOR_INCIDENT: dict[IncidentType, ResponderSkill] = {
    IncidentType.MEDICAL: ResponderSkill.MEDICAL,
    IncidentType.TRAPPED_PERSONS: ResponderSkill.RESCUE,
    IncidentType.STRUCTURAL_COLLAPSE: ResponderSkill.RESCUE,
    IncidentType.FIRE: ResponderSkill.RESCUE,
    IncidentType.FLOODING: ResponderSkill.STRUCTURAL,
    IncidentType.LANDSLIDE: ResponderSkill.STRUCTURAL,
    IncidentType.INFRASTRUCTURE: ResponderSkill.STRUCTURAL,
    # Not in the TRD's mapping. An unclassified incident gets the general-purpose unit
    # rather than no requirement at all, so it still competes on distance and load.
    IncidentType.OTHER: ResponderSkill.RESCUE,
}

EXACT_SKILL = 1.0
COMPATIBLE_SKILL = 0.6
MISMATCHED_SKILL = 0.2

# Which skills can stand in for which. Rescue crews carry first aid and structural
# crews can extricate, so neither is useless off its speciality; a medical unit at a
# building collapse is.
COMPATIBLE_WITH: dict[ResponderSkill, frozenset[ResponderSkill]] = {
    ResponderSkill.RESCUE: frozenset({ResponderSkill.STRUCTURAL}),
    ResponderSkill.STRUCTURAL: frozenset({ResponderSkill.RESCUE}),
    ResponderSkill.MEDICAL: frozenset({ResponderSkill.RESCUE}),
}


class DispatchOutcome(str, Enum):
    ASSIGNED = "assigned"
    DEFERRED = "deferred"
    QUEUE_EMPTY = "queue_empty"


@dataclass(frozen=True)
class MatchBreakdown:
    """Why this responder and not another."""

    distance_km: float
    distance_component: float
    skill_component: float
    load_component: float
    score: float
    required_skill: ResponderSkill

    def as_dict(self) -> dict[str, float | str]:
        return {
            "distance_km": round(self.distance_km, 3),
            "distance_component": round(self.distance_component, 4),
            "skill_component": self.skill_component,
            "load_component": round(self.load_component, 4),
            "required_skill": self.required_skill.value,
            "score": self.score,
        }


@dataclass
class DispatchResult:
    outcome: DispatchOutcome
    report: Report | None = None
    responder: Responder | None = None
    assignment: Assignment | None = None
    match: MatchBreakdown | None = None
    reason: str | None = None
    candidates_considered: int = 0


def required_skill(incident_type: IncidentType | None) -> ResponderSkill:
    if incident_type is None:
        return ResponderSkill.RESCUE
    return SKILL_FOR_INCIDENT.get(incident_type, ResponderSkill.RESCUE)


def skill_component(required: ResponderSkill, actual: ResponderSkill) -> float:
    if actual == required:
        return EXACT_SKILL
    if actual in COMPATIBLE_WITH.get(required, frozenset()):
        return COMPATIBLE_SKILL
    return MISMATCHED_SKILL


def score_match(report: Report, responder: Responder) -> MatchBreakdown:
    """Score one responder against one report. Assumes the candidate filter has passed."""
    needed = required_skill(report.incident_type)
    distance = haversine_km(report.lat, report.lng, responder.lat, responder.lng)

    distance_part = max(0.0, 1.0 - distance / settings.dispatch_max_radius_km)
    skill_part = skill_component(needed, responder.skill)
    load_part = max(0.0, 1.0 - responder.active_count / max(1, responder.capacity))

    score = (
        settings.dispatch_weight_distance * distance_part
        + settings.dispatch_weight_skill * skill_part
        + settings.dispatch_weight_load * load_part
    )

    return MatchBreakdown(
        distance_km=distance,
        distance_component=distance_part,
        skill_component=skill_part,
        load_component=load_part,
        score=round(score, 4),
        required_skill=needed,
    )


def responders_who_rejected(session: Session, report_id: uuid.UUID) -> set[uuid.UUID]:
    """Crews that have already turned this report down.

    Offering it straight back would be a loop: a rejected report returns to the queue,
    the same best-fit responder wins again, and they reject again. Their refusal is
    information, so it is honoured.
    """
    return {
        assignment.responder_id
        for assignment in session.exec(
            select(Assignment).where(Assignment.report_id == report_id)
        ).all()
        if assignment.rejected_at is not None
    }


def find_candidates(session: Session, report: Report) -> list[tuple[Responder, MatchBreakdown]]:
    """Available, under capacity, and in range — scored and sorted best first."""
    eligible = session.exec(
        select(Responder).where(Responder.status == ResponderStatus.AVAILABLE)
    ).all()

    refused = responders_who_rejected(session, report.id)

    scored: list[tuple[Responder, MatchBreakdown]] = []
    for responder in eligible:
        if responder.id in refused:
            continue
        # Belt and braces: status alone should imply spare capacity, but a responder
        # must never be assigned beyond it (FR-20).
        if responder.active_count >= responder.capacity:
            continue
        match = score_match(report, responder)
        if match.distance_km > settings.dispatch_max_radius_km:
            continue
        scored.append((responder, match))

    # Ties break on responder id so repeated dispatches are reproducible.
    scored.sort(key=lambda pair: (-pair[1].score, str(pair[0].id)))
    return scored


def best_match(session: Session, report: Report) -> tuple[Responder, MatchBreakdown] | None:
    candidates = find_candidates(session, report)
    return candidates[0] if candidates else None


def _occupy(responder: Responder) -> None:
    """Take one unit of capacity, marking the responder busy once it is full."""
    responder.active_count += 1
    if responder.active_count >= responder.capacity and responder.status == ResponderStatus.AVAILABLE:
        responder.status = ResponderStatus.BUSY


def assign_report(
    session: Session,
    report: Report,
    operator: str | None = None,
    *,
    emit_deferral: bool = True,
) -> DispatchResult:
    """Assign one queued report to its best-fit responder, or defer it.

    Does not commit — the caller owns the transaction, so the assignment, the capacity
    change, and the event all land together or not at all.

    ``emit_deferral`` exists because ``dispatch_next`` keeps looking further down the
    queue after a failure. Logging a deferral for every report it passes over would
    bury the one that matters — the report that was actually up next — under dozens of
    identical events.
    """
    candidates = find_candidates(session, report)
    actor = f"operator:{operator}" if operator else "system"

    if not candidates:
        # The report keeps its place and its accrued wait time; only the attempt failed.
        if emit_deferral:
            emit_event(
                session,
                case_id=report.id,
                activity=Activity.DISPATCH_DEFERRED,
                resource=actor,
                metadata={
                    "reason": "no available responder in range with spare capacity",
                    "required_skill": required_skill(report.incident_type).value,
                    "radius_km": settings.dispatch_max_radius_km,
                },
            )
            logger.info(
                "dispatch deferred",
                extra={
                    "report_id": str(report.id),
                    "required_skill": required_skill(report.incident_type).value,
                },
            )
        return DispatchResult(
            outcome=DispatchOutcome.DEFERRED,
            report=report,
            reason="No available responder within range has spare capacity",
            candidates_considered=0,
        )

    responder, match = candidates[0]

    assignment = Assignment(
        report_id=report.id, responder_id=responder.id, assigned_at=utcnow()
    )
    session.add(assignment)

    _occupy(responder)
    session.add(responder)

    report.status = ReportStatus.ASSIGNED
    session.add(report)

    emit_event(
        session,
        case_id=report.id,
        activity=Activity.ASSIGNED,
        resource=actor,
        metadata={
            "responder_id": str(responder.id),
            "responder_name": responder.name,
            "match": match.as_dict(),
            "candidates_considered": len(candidates),
        },
    )

    logger.info(
        "report assigned",
        extra={
            "report_id": str(report.id),
            "responder": responder.name,
            "match_score": match.score,
            "distance_km": round(match.distance_km, 3),
            "candidates": len(candidates),
        },
    )

    return DispatchResult(
        outcome=DispatchOutcome.ASSIGNED,
        report=report,
        responder=responder,
        assignment=assignment,
        match=match,
        candidates_considered=len(candidates),
    )


def dispatch_next(session: Session, operator: str | None = None) -> DispatchResult:
    """Assign the highest-priority report in the queue.

    Walks down the queue rather than stopping at the head: if the top report has no
    responder in range, the one below it may, and refusing to look would idle a crew
    that could be helping someone.
    """
    queue = build_queue(session)
    if not queue:
        return DispatchResult(outcome=DispatchOutcome.QUEUE_EMPTY, reason="The queue is empty")

    first_deferred: DispatchResult | None = None

    for index, entry in enumerate(queue):
        # Only the head of the queue records a deferral; the rest are just candidates
        # we looked at on the way past.
        result = assign_report(session, entry.report, operator, emit_deferral=index == 0)
        if result.outcome == DispatchOutcome.ASSIGNED:
            return result
        if first_deferred is None:
            first_deferred = result

    return first_deferred or DispatchResult(
        outcome=DispatchOutcome.DEFERRED, reason="No report could be assigned"
    )


def dispatch_batch(session: Session, limit: int, operator: str | None = None) -> list[DispatchResult]:
    """Assign up to `limit` reports, stopping when nothing more can be placed."""
    results: list[DispatchResult] = []

    for _ in range(max(0, limit)):
        result = dispatch_next(session, operator)
        if result.outcome != DispatchOutcome.ASSIGNED:
            if result.outcome == DispatchOutcome.DEFERRED:
                results.append(result)
            break
        results.append(result)

    return results


def open_assignment_for(session: Session, report_id: uuid.UUID) -> Assignment | None:
    """The assignment currently holding a responder for this report, if any."""
    for assignment in session.exec(
        select(Assignment).where(Assignment.report_id == report_id)
    ).all():
        if assignment.is_open:
            return assignment
    return None


# --- Responder lifecycle (Phase 8) ------------------------------------------------------

# Which lifecycle status each transition should be recorded as.
STATUS_ACTIVITY: dict[ReportStatus, Activity] = {
    ReportStatus.ACKNOWLEDGED: Activity.ACKNOWLEDGED,
    ReportStatus.EN_ROUTE: Activity.EN_ROUTE,
    ReportStatus.ON_SCENE: Activity.ON_SCENE,
    ReportStatus.RESOLVED: Activity.RESOLVED,
    ReportStatus.CLOSED: Activity.CLOSED,
}


def release_capacity(responder: Responder) -> None:
    """Give a responder their slot back.

    Only ``busy`` returns to ``available``: a unit an operator took ``offline`` must
    stay offline, however its workload changes.
    """
    responder.active_count = max(0, responder.active_count - 1)
    if responder.status == ResponderStatus.BUSY and responder.active_count < responder.capacity:
        responder.status = ResponderStatus.AVAILABLE


def advance_assignment(
    session: Session,
    assignment: Assignment,
    report: Report,
    responder: Responder,
    target: ReportStatus,
    actor: str,
    note: str | None = None,
) -> None:
    """Move a report along its lifecycle. Caller has already checked the transition.

    Capacity is released at ``resolved`` rather than ``closed``: the crew is free the
    moment they finish on scene, and holding their slot through the paperwork would
    idle a unit that could be dispatched.
    """
    now = utcnow()
    report.status = target

    if target == ReportStatus.ACKNOWLEDGED:
        assignment.acknowledged_at = now
    elif target == ReportStatus.RESOLVED:
        assignment.resolved_at = now
        release_capacity(responder)
        session.add(responder)

    session.add(assignment)
    session.add(report)

    emit_event(
        session,
        case_id=report.id,
        activity=STATUS_ACTIVITY[target],
        resource=actor,
        metadata={
            "assignment_id": str(assignment.id),
            "responder_id": str(responder.id),
            "responder_name": responder.name,
            "note": note,
        },
    )

    logger.info(
        "assignment advanced",
        extra={
            "report_id": str(report.id),
            "assignment_id": str(assignment.id),
            "status": target.value,
            "actor": actor,
        },
    )


def reject_assignment(
    session: Session,
    assignment: Assignment,
    report: Report,
    responder: Responder,
    reason: str,
    actor: str,
) -> None:
    """A responder declines; the report returns to the queue (FR-21).

    ``client_created_at`` is deliberately untouched, so the report resumes with the wait
    time it already accrued rather than starting from zero. A report bounced between
    crews would otherwise be punished for their unavailability.
    """
    assignment.rejected_at = utcnow()
    assignment.rejection_reason = reason
    session.add(assignment)

    release_capacity(responder)
    session.add(responder)

    report.status = ReportStatus.QUEUED
    session.add(report)

    emit_event(
        session,
        case_id=report.id,
        activity=Activity.ASSIGNMENT_REJECTED,
        resource=actor,
        metadata={
            "assignment_id": str(assignment.id),
            "responder_id": str(responder.id),
            "responder_name": responder.name,
            "reason": reason,
        },
    )

    logger.info(
        "assignment rejected",
        extra={
            "report_id": str(report.id),
            "responder": responder.name,
            "reason": reason,
        },
    )
