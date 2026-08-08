"""The ProcessEvent table — the append-only process-mining log.

Never updated, never deleted (TRD §10). Column names follow process-mining convention
so the CSV export in Phase 9 is a straight projection: case_id, activity, timestamp,
resource.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, String
from sqlmodel import Field, SQLModel

from app.core.time import utcnow


class ProcessEvent(SQLModel, table=True):
    __tablename__ = "process_event"
    __table_args__ = (
        # Replaying one case's trail in order — the common read (Phase 9).
        Index("ix_process_event_case_time", "case_id", "timestamp"),
        # Cycle-time medians are grouped by activity across cases (TRD §4.5).
        Index("ix_process_event_activity_time", "activity", "timestamp"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # The process-mining case identifier: always the report's id.
    case_id: uuid.UUID = Field(foreign_key="report.id", index=True)

    # Plain string, not a constrained enum: the log is append-only and later phases
    # introduce new activities, which a CHECK constraint would require a migration for.
    activity: str = Field(sa_column=Column(String(64), nullable=False, index=True))

    # Who acted: "system", an operator id, or a responder id.
    resource: str = Field(default="system", max_length=64, index=True)

    timestamp: datetime = Field(default_factory=utcnow, index=True)

    # Mapped to the conventional column name `metadata`; the Python attribute cannot be
    # called that because SQLAlchemy's declarative base already owns `metadata`.
    event_metadata: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column("metadata", JSON, nullable=False)
    )
