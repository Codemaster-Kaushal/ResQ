"""Request and response models for offline batch sync (FR-26 … FR-28)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.core.time import to_naive_utc
from app.models.enums import ReportStatus


class SyncOutcome(str, Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class SyncReportItem(BaseModel):
    """One report queued on a device while it had no connectivity.

    Coordinates are deliberately *not* range-constrained here. Pydantic validates the
    whole body at once, so a single bad value would fail the entire batch — and losing
    nineteen good reports because the twentieth has a corrupt GPS fix is the opposite of
    what offline sync is for. Ranges are checked per item, and only that item is
    rejected.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        description="Generated on the device when the report was filed. The dedup key.",
    )
    text: str = Field(min_length=1, max_length=5000)
    lat: float
    lng: float
    client_created_at: datetime | None = Field(
        default=None, description="When the reporter filed it. Drives wait time (FR-28)."
    )
    reporter_pseudonym: str | None = Field(default=None, max_length=64)

    def normalised_client_time(self) -> datetime | None:
        return None if self.client_created_at is None else to_naive_utc(self.client_created_at)


class SyncBatch(BaseModel):
    reports: list[SyncReportItem] = Field(
        min_length=1, description="The device's queued reports, in any order"
    )
    device_id: str | None = Field(
        default=None, max_length=64, description="Optional label, recorded on the event log"
    )


class SyncItemResult(BaseModel):
    idempotency_key: str
    outcome: SyncOutcome
    report_id: uuid.UUID | None = None
    status: ReportStatus | None = None
    client_created_at: datetime | None = None
    minutes_waiting: float | None = Field(
        default=None, description="Wait already accrued on the client clock"
    )
    client_timestamp_supplied: bool = True
    error_code: str | None = None
    error_message: str | None = None


class SyncResult(BaseModel):
    received: int
    created: int
    duplicates: int
    rejected: int
    results: list[SyncItemResult]
