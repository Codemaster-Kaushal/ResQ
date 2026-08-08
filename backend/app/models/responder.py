"""The Responder table — field units available for dispatch.

See the note in ``report.py`` on why these modules avoid deferred annotations.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Column, Index
from sqlmodel import Field, Relationship, SQLModel

from app.models.columns import enum_type
from app.models.enums import ResponderSkill, ResponderStatus

if TYPE_CHECKING:
    from app.models.assignment import Assignment


class Responder(SQLModel, table=True):
    __tablename__ = "responder"
    __table_args__ = (
        # Candidate filter: available, under capacity, then distance (TRD §4.4).
        Index("ix_responder_status_skill", "status", "skill"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(max_length=128, index=True)

    skill: ResponderSkill = Field(
        sa_column=Column(enum_type(ResponderSkill), nullable=False, index=True)
    )

    lat: float
    lng: float

    capacity: int = Field(default=1, ge=1)

    # Denormalised count of open assignments. Dispatch never exceeds capacity (FR-20).
    active_count: int = Field(default=0, ge=0)

    status: ResponderStatus = Field(
        default=ResponderStatus.AVAILABLE,
        sa_column=Column(enum_type(ResponderStatus), nullable=False, index=True),
    )

    assignments: list["Assignment"] = Relationship(back_populates="responder")

    @property
    def has_spare_capacity(self) -> bool:
        return self.active_count < self.capacity

    @property
    def is_dispatchable(self) -> bool:
        """Candidate filter from TRD §4.4, minus the distance test."""
        return self.status == ResponderStatus.AVAILABLE and self.has_spare_capacity
