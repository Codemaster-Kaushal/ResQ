"""Domain enumerations and the report status lifecycle.

Values are lowercase snake_case and are what the API emits and the database stores.
"""

from __future__ import annotations

from enum import Enum


class IncidentType(str, Enum):
    """Fixed triage taxonomy (FR-6). Never widened at runtime."""

    STRUCTURAL_COLLAPSE = "structural_collapse"
    FLOODING = "flooding"
    MEDICAL = "medical"
    TRAPPED_PERSONS = "trapped_persons"
    FIRE = "fire"
    LANDSLIDE = "landslide"
    INFRASTRUCTURE = "infrastructure"
    OTHER = "other"


class ReportStatus(str, Enum):
    """Report lifecycle states (TRD §3)."""

    RECEIVED = "received"
    CLASSIFIED = "classified"
    VERIFIED = "verified"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    ACKNOWLEDGED = "acknowledged"
    EN_ROUTE = "en_route"
    ON_SCENE = "on_scene"
    RESOLVED = "resolved"
    CLOSED = "closed"

    # Human-review branch
    FLAGGED = "flagged"
    REJECTED = "rejected"


class ResponderSkill(str, Enum):
    MEDICAL = "medical"
    RESCUE = "rescue"
    STRUCTURAL = "structural"


class ResponderStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"


class Activity(str, Enum):
    """Process-event activity names (TRD §3, §6).

    Stored in a plain string column rather than a constrained one: the event log is
    append-only and later phases add activities, and a CHECK constraint would need a
    migration every time one is introduced.
    """

    REPORT_RECEIVED = "REPORT_RECEIVED"
    TRIAGE_COMPLETED = "TRIAGE_COMPLETED"
    AUTHENTICITY_SCORED = "AUTHENTICITY_SCORED"
    REPORT_FLAGGED = "REPORT_FLAGGED"
    REPORT_VERIFIED = "REPORT_VERIFIED"
    REPORT_REJECTED = "REPORT_REJECTED"
    QUEUED = "QUEUED"
    PRIORITY_OVERRIDDEN = "PRIORITY_OVERRIDDEN"
    ASSIGNED = "ASSIGNED"
    DISPATCH_DEFERRED = "DISPATCH_DEFERRED"
    ASSIGNMENT_REJECTED = "ASSIGNMENT_REJECTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EN_ROUTE = "EN_ROUTE"
    ON_SCENE = "ON_SCENE"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


# Legal status transitions (TRD §3). Enforced in Phase 8; declared here because the
# lifecycle is part of the data model, not of any one service.
LEGAL_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.RECEIVED: frozenset({ReportStatus.CLASSIFIED}),
    ReportStatus.CLASSIFIED: frozenset({ReportStatus.VERIFIED, ReportStatus.FLAGGED}),
    # Human review resolves a flagged report either way (FR-15). Rejection is only
    # ever reachable from here — no automated path may reach it.
    ReportStatus.FLAGGED: frozenset({ReportStatus.VERIFIED, ReportStatus.REJECTED}),
    ReportStatus.VERIFIED: frozenset({ReportStatus.QUEUED}),
    # A rejected assignment returns the report to the queue (FR-21).
    ReportStatus.QUEUED: frozenset({ReportStatus.ASSIGNED}),
    ReportStatus.ASSIGNED: frozenset({ReportStatus.ACKNOWLEDGED, ReportStatus.QUEUED}),
    ReportStatus.ACKNOWLEDGED: frozenset({ReportStatus.EN_ROUTE, ReportStatus.QUEUED}),
    ReportStatus.EN_ROUTE: frozenset({ReportStatus.ON_SCENE, ReportStatus.QUEUED}),
    ReportStatus.ON_SCENE: frozenset({ReportStatus.RESOLVED}),
    ReportStatus.RESOLVED: frozenset({ReportStatus.CLOSED}),
    ReportStatus.CLOSED: frozenset(),
    ReportStatus.REJECTED: frozenset(),
}

TERMINAL_STATUSES = frozenset({ReportStatus.CLOSED, ReportStatus.REJECTED})


def is_legal_transition(current: ReportStatus, target: ReportStatus) -> bool:
    return target in LEGAL_TRANSITIONS.get(current, frozenset())
