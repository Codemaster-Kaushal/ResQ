"""SQLModel table definitions.

Importing this package registers every model on ``SQLModel.metadata``, which is what
``init_db()`` creates tables from and what resolves the string-annotated relationships.
Import models from here rather than from their individual modules.
"""

from app.models.assignment import Assignment
from app.models.enums import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATUSES,
    Activity,
    IncidentType,
    ReportStatus,
    ResponderSkill,
    ResponderStatus,
    is_legal_transition,
)
from app.models.process_event import ProcessEvent
from app.models.report import Report
from app.models.responder import Responder

__all__ = [
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATUSES",
    "Activity",
    "Assignment",
    "IncidentType",
    "ProcessEvent",
    "Report",
    "ReportStatus",
    "Responder",
    "ResponderSkill",
    "ResponderStatus",
    "is_legal_transition",
]
