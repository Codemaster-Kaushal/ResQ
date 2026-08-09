"""Request and response models for the event log and process mining."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    case_id: uuid.UUID
    activity: str
    resource: str
    timestamp: datetime

    # Read from `event_metadata`, emitted as `metadata`. Reading the attribute called
    # `metadata` off a SQLModel row returns SQLAlchemy's MetaData object, not the JSON
    # column — the same reserved-name collision the model itself sidesteps.
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="event_metadata")


class EventPage(BaseModel):
    items: list[EventRead]
    total: int
    limit: int
    offset: int


class TransitionRead(BaseModel):
    transition: str
    from_activity: str
    to_activity: str | None = None
    closed_cases: int = Field(description="Completed cases this median was learned from")
    median_minutes: float
    open_cases: int = Field(description="Cases currently sitting at this stage")
    current_mean_minutes: float
    deviation_ratio: float = Field(description="current_mean / median; 0 when no baseline")
    is_bottleneck: bool
    suggested_action: str | None = None


class BottleneckReport(BaseModel):
    generated_at: datetime
    closed_cases: int
    open_cases: int
    deviation_threshold: float
    bottlenecks: list[TransitionRead]
    transitions: list[TransitionRead]
    note: str | None = None
