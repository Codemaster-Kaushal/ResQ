"""Request and response models for dispatch."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IncidentType, ResponderSkill
from app.services.dispatch import DispatchOutcome


class MatchBreakdownRead(BaseModel):
    """Why this responder won, in the operator's terms."""

    distance_km: float
    distance_component: float
    skill_component: float
    load_component: float
    required_skill: ResponderSkill
    score: float


class AssignedResponder(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    skill: ResponderSkill
    active_count: int
    capacity: int


class AssignedReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idempotency_key: str
    incident_type: IncidentType | None = None
    severity_score: int | None = None
    priority_score: float | None = None
    status: str


class DispatchRequest(BaseModel):
    """Assign the top of the queue, or one named report."""

    model_config = ConfigDict(str_strip_whitespace=True)

    report_id: uuid.UUID | None = Field(
        default=None, description="Omit to take the highest-priority report in the queue"
    )
    operator: str | None = Field(
        default=None, max_length=64, description="Recorded on the process event"
    )


class DispatchResponse(BaseModel):
    outcome: DispatchOutcome
    assignment_id: uuid.UUID | None = None
    assigned_at: datetime | None = None
    report: AssignedReport | None = None
    responder: AssignedResponder | None = None
    match: MatchBreakdownRead | None = None
    candidates_considered: int = 0
    reason: str | None = Field(
        default=None, description="Why nothing was assigned, when nothing was"
    )
