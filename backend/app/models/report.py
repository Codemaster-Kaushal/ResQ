"""The Report table — the central case record.

These model modules deliberately omit ``from __future__ import annotations``: SQLModel
inspects annotations at runtime to build relationships, and deferred (string)
annotations leave it unable to resolve the related class. PEP 604 unions such as
``str | None`` evaluate natively on Python 3.10, so nothing is lost.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field, Relationship, SQLModel

from app.core.time import utcnow
from app.models.columns import enum_type
from app.models.enums import IncidentType, ReportStatus

if TYPE_CHECKING:
    from app.models.assignment import Assignment


class Report(SQLModel, table=True):
    """A citizen report and every score derived from it.

    Note on the JSON reason lists: assign a new list rather than mutating in place
    (``report.severity_reasons = [...]``). SQLAlchemy does not track in-place mutation
    of a plain JSON column, so an ``.append()`` would silently fail to persist.
    """

    __tablename__ = "report"
    __table_args__ = (
        # Queue reads: filter by status, order by priority (Phase 6).
        Index("ix_report_status_priority", "status", "priority_score"),
        # Corroboration window: nearby reports within a time window (TRD §4.2).
        Index("ix_report_geo_time", "lat", "lng", "client_created_at"),
        # Impossible-movement check: one pseudonym's reports in time order (TRD §4.2).
        Index("ix_report_pseudonym_time", "reporter_pseudonym", "client_created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # Client-generated. The dedup key that makes offline batch sync idempotent (FR-27).
    idempotency_key: str = Field(unique=True, index=True, max_length=128)

    text: str
    image_path: str | None = Field(default=None, max_length=512)
    image_phash: str | None = Field(default=None, index=True, max_length=64)

    lat: float
    lng: float

    # Ageing and wait time always use the *client* clock, so a report filed offline an
    # hour ago is not penalised for syncing late (FR-28, TRD §4.3).
    client_created_at: datetime = Field(index=True)
    received_at: datetime = Field(default_factory=utcnow, index=True)

    # NFR-7: pseudonymous. No name, phone, or email is ever stored.
    reporter_pseudonym: str = Field(max_length=64, index=True)

    incident_type: IncidentType | None = Field(
        default=None,
        sa_column=Column(enum_type(IncidentType), nullable=True, index=True),
    )
    severity_score: int | None = Field(default=None, index=True)
    severity_reasons: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )

    authenticity_score: int | None = Field(default=None, index=True)
    authenticity_reasons: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )

    status: ReportStatus = Field(
        default=ReportStatus.RECEIVED,
        sa_column=Column(enum_type(ReportStatus), nullable=False, index=True),
    )

    # Recomputed on read rather than trusted from storage (TRD §4.3): ageing moves it.
    priority_score: float | None = Field(default=None)

    scoring_provider: str | None = Field(default=None, max_length=32)

    # Operator pin. Sorts above every computed score (FR-18).
    manual_override_rank: int | None = Field(default=None, index=True)

    assignments: list["Assignment"] = Relationship(back_populates="report")
