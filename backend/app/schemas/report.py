"""Request and response models for report ingestion and retrieval."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.time import to_naive_utc, utcnow
from app.models.enums import IncidentType, ReportStatus
from app.services.media import ExifSnapshot, StoredImage


class ReportCreate(BaseModel):
    """The multipart form body of ``POST /api/reports`` (FR-1).

    Coordinate bounds are enforced here so an out-of-range value is rejected with the
    typed validation envelope naming the offending field, before anything is written.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=5000, description="Free-text description")
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)

    client_created_at: datetime | None = Field(
        default=None,
        description="Time on the reporter's device. Defaults to server receipt time.",
    )
    reporter_pseudonym: str | None = Field(
        default=None,
        max_length=64,
        description="Pseudonymous handle. Generated when omitted — never a real identity.",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated key. Re-posting the same key returns the first report.",
    )

    @field_validator("client_created_at")
    @classmethod
    def _normalise_timestamp(cls, value: datetime | None) -> datetime | None:
        # Client offsets are applied and dropped here; everything downstream is naive UTC.
        return None if value is None else to_naive_utc(value)

    @field_validator("reporter_pseudonym", "idempotency_key")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value or None

    def resolved_pseudonym(self) -> str:
        """NFR-7: a report never requires an identity to be filed."""
        return self.reporter_pseudonym or f"anon-{secrets.token_hex(4)}"

    def resolved_idempotency_key(self) -> str:
        return self.idempotency_key or f"srv-{uuid.uuid4().hex}"

    def resolved_client_created_at(self) -> datetime:
        return self.client_created_at or utcnow()


class ExifRead(BaseModel):
    """EXIF as read from the stored file (FR-5)."""

    captured_at: datetime | None = None
    lat: float | None = None
    lng: float | None = None
    camera: str | None = None
    has_gps: bool = False

    @classmethod
    def from_snapshot(cls, snapshot: ExifSnapshot) -> "ExifRead | None":
        if snapshot.is_empty:
            return None
        return cls(
            captured_at=snapshot.captured_at,
            lat=snapshot.lat,
            lng=snapshot.lng,
            camera=snapshot.camera,
            has_gps=snapshot.has_gps,
        )


class ImageRead(BaseModel):
    path: str = Field(description="Path relative to the media root")
    phash: str = Field(description="Perceptual hash used for duplicate detection")
    byte_size: int | None = None
    exif: ExifRead | None = None

    @classmethod
    def from_stored(cls, stored: StoredImage) -> "ImageRead":
        return cls(
            path=stored.relative_path,
            phash=stored.phash,
            byte_size=stored.byte_size,
            exif=ExifRead.from_snapshot(stored.exif),
        )


class ReportCreated(BaseModel):
    """FR-3: the server id plus an echo of the client's idempotency key."""

    id: uuid.UUID
    idempotency_key: str
    status: ReportStatus
    received_at: datetime
    client_created_at: datetime
    reporter_pseudonym: str
    duplicate: bool = Field(
        default=False,
        description="True when this key was already on file; no second report was created.",
    )
    image: ImageRead | None = None


class ReportRead(BaseModel):
    """Full detail, including scores and the reasons behind them (FR-8)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idempotency_key: str
    text: str
    lat: float
    lng: float
    client_created_at: datetime
    received_at: datetime
    reporter_pseudonym: str

    incident_type: IncidentType | None = None
    severity_score: int | None = None
    severity_reasons: list[dict[str, Any]] = Field(default_factory=list)
    authenticity_score: int | None = None
    authenticity_reasons: list[dict[str, Any]] = Field(default_factory=list)

    status: ReportStatus
    priority_score: float | None = None
    scoring_provider: str | None = None
    manual_override_rank: int | None = None

    image: ImageRead | None = None


class ReportSummary(BaseModel):
    """List projection — omits the reason arrays to keep pages small."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idempotency_key: str
    text: str
    lat: float
    lng: float
    client_created_at: datetime
    received_at: datetime
    reporter_pseudonym: str
    incident_type: IncidentType | None = None
    severity_score: int | None = None
    authenticity_score: int | None = None
    status: ReportStatus
    priority_score: float | None = None
    has_image: bool = False


class ReportPage(BaseModel):
    items: list[ReportSummary]
    total: int = Field(description="Total matching the filters, ignoring pagination")
    limit: int
    offset: int
