"""The Assignment table — one attempt to send a responder to a report.

A report can have several assignments over its life: a rejected assignment returns the
report to the queue (FR-21) and a later assignment takes its place. The rejected row is
kept, because the rejection is part of the process history.

See the note in ``report.py`` on why these modules avoid deferred annotations.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Index
from sqlmodel import Field, Relationship, SQLModel

from app.core.time import utcnow

if TYPE_CHECKING:
    from app.models.report import Report
    from app.models.responder import Responder


class Assignment(SQLModel, table=True):
    __tablename__ = "assignment"
    __table_args__ = (Index("ix_assignment_report_assigned", "report_id", "assigned_at"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    report_id: uuid.UUID = Field(foreign_key="report.id", index=True)
    responder_id: uuid.UUID = Field(foreign_key="responder.id", index=True)

    # The en_route and on_scene milestones live only in the ProcessEvent log, which is
    # what §4.5 mines for cycle times. Duplicating them here would create a second
    # source of truth that can drift from it.
    assigned_at: datetime = Field(default_factory=utcnow, index=True)
    acknowledged_at: datetime | None = Field(default=None)
    resolved_at: datetime | None = Field(default=None)

    rejected_at: datetime | None = Field(default=None)
    rejection_reason: str | None = Field(default=None, max_length=512)

    report: "Report" = Relationship(back_populates="assignments")
    responder: "Responder" = Relationship(back_populates="assignments")

    @property
    def is_open(self) -> bool:
        """Counts against the responder's capacity while true."""
        return self.resolved_at is None and self.rejected_at is None
