"""The scoring provider contract (TRD §5).

A provider's only job is to *extract signals* from a report. It never returns a
severity score — weighting is `services/triage.py`'s responsibility, so the arithmetic
is identical whichever provider answered, and swapping providers can never silently
change how severe an incident is judged to be.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import IncidentType

LOCAL_PROVIDER_NAME = "local"


class ProviderError(Exception):
    """A provider could not produce a usable result. Always caught by the router."""


class TriageResult(BaseModel):
    """What a provider extracted from one report.

    Numeric fields are clamped rather than rejected — a model that says the modifier is
    15 still gave usable information, and discarding the whole extraction over one
    out-of-range integer would be a poor trade. ``incident_type`` is strict, however: a
    provider that cannot pick from the fixed taxonomy is not one to trust, and failing
    validation drops it to the next provider in the chain.
    """

    model_config = ConfigDict(extra="ignore")

    incident_type: IncidentType = IncidentType.OTHER
    life_risk_terms: list[str] = Field(default_factory=list)
    people_affected_estimate: int | None = Field(default=None, ge=0)
    vulnerability_terms: list[str] = Field(default_factory=list)
    visual_severity_modifier: int = 0
    confidence: float = 0.5

    @field_validator("visual_severity_modifier", mode="before")
    @classmethod
    def _clamp_modifier(cls, value: object) -> int:
        try:
            return max(-10, min(10, int(float(value))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5

    @field_validator("people_affected_estimate", mode="before")
    @classmethod
    def _sane_head_count(cls, value: object) -> int | None:
        """Guard against a model hallucinating an implausible figure."""
        if value is None or value == "":
            return None
        try:
            count = int(float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return max(0, min(100_000, count))

    @field_validator("life_risk_terms", "vulnerability_terms", mode="before")
    @classmethod
    def _coerce_term_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if item is not None]
        return []


@runtime_checkable
class ScoringProvider(Protocol):
    """Implemented by the local scorer and by every remote model."""

    name: str

    def is_available(self) -> bool:
        """False when the provider has no credentials, so the router skips it fast."""
        ...

    async def classify(self, text: str, image_bytes: bytes | None) -> TriageResult:
        """Extract signals, or raise ProviderError."""
        ...
