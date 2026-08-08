"""Request and response models for the priority queue."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import IncidentType, ReportStatus


class PriorityBreakdownRead(BaseModel):
    """The components behind a queue position, so an operator can argue with it."""

    severity: int
    authenticity: int
    ageing_bonus: float
    minutes_waiting: float
    score: float


class QueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    id: uuid.UUID
    idempotency_key: str
    text: str
    incident_type: IncidentType | None = None
    status: ReportStatus
    lat: float
    lng: float
    client_created_at: datetime
    received_at: datetime
    priority_score: float
    priority: PriorityBreakdownRead
    manual_override_rank: int | None = None
    pinned: bool = False
    demoted: bool = False


class QueuePage(BaseModel):
    items: list[QueueItem]
    total: int = Field(description="Reports in the queue, ignoring pagination")
    limit: int
    offset: int
    generated_at: datetime = Field(
        description="Ageing moves with the clock, so a queue read is a snapshot"
    )


class OverrideAction(str, Enum):
    PIN = "pin"
    DEMOTE = "demote"
    CLEAR = "clear"


class OverrideRequest(BaseModel):
    """An operator reordering the queue by hand (FR-18).

    The operator's identity is required, not optional: the override is recorded as a
    process event, and an anonymous one would evidence nothing.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    action: OverrideAction
    operator: str = Field(min_length=1, max_length=64)
    rank: int | None = Field(
        default=None,
        description="Explicit position within the pinned or demoted band. Defaults to the top.",
    )
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _rank_only_where_it_applies(self) -> "OverrideRequest":
        if self.action == OverrideAction.CLEAR and self.rank is not None:
            raise ValueError("rank cannot be supplied when clearing an override")
        if self.rank is not None and self.rank < 0:
            raise ValueError("rank must be zero or positive; direction comes from the action")
        return self


class OverrideResult(BaseModel):
    id: uuid.UUID
    action: OverrideAction
    operator: str
    reason: str | None = None
    manual_override_rank: int | None = None
    previous_rank: int | None = None
    previous_position: int | None = None
    position: int | None = None
    event_id: uuid.UUID
