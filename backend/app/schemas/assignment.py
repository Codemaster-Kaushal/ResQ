"""Request and response models for the responder lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ReportStatus, ResponderSkill, ResponderStatus


class StatusUpdateRequest(BaseModel):
    """A responder moving their assignment along (FR-22)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    status: ReportStatus
    actor: str = Field(
        default="responder",
        max_length=64,
        description="Who reported the change; recorded on the process event",
    )
    note: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    """A responder declining an assignment (FR-21).

    A reason is required. A rejection with no explanation tells the control room
    nothing, and the report is about to go back into the queue on the strength of it.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=1000)
    actor: str = Field(default="responder", max_length=64)


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_id: uuid.UUID
    responder_id: uuid.UUID
    responder_name: str | None = None
    report_status: ReportStatus | None = None
    assigned_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    is_open: bool = True


class LifecycleResult(BaseModel):
    assignment: AssignmentRead
    report_status: ReportStatus
    previous_status: ReportStatus
    responder_active_count: int
    responder_status: ResponderStatus
    queue_position: int | None = Field(
        default=None, description="Set when a rejection returns the report to the queue"
    )
    minutes_waiting: float | None = Field(
        default=None, description="Wait accrued since the report was filed, on the client clock"
    )


class ResponderRead(BaseModel):
    """A responder with live load (FR-20 visibility)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    skill: ResponderSkill
    status: ResponderStatus
    lat: float
    lng: float
    capacity: int
    active_count: int

    # Derived after validation — the ORM row does not carry them.
    spare_capacity: int = 0
    dispatchable: bool = False
    open_assignments: int = Field(
        default=0, description="Assignments this system is currently tracking for the responder"
    )


class ResponderPage(BaseModel):
    items: list[ResponderRead]
    total: int
    limit: int
    offset: int
