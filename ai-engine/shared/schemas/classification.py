"""
Classification enums and schemas for RescueNet AI.
Defines the fixed 8-category incident taxonomy (FR-6).
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class IncidentType(str, Enum):
    """Fixed 8-category incident taxonomy — FR-6."""
    STRUCTURAL_COLLAPSE = "structural_collapse"
    FLOODING = "flooding"
    MEDICAL = "medical"
    TRAPPED_PERSONS = "trapped_persons"
    FIRE = "fire"
    LANDSLIDE = "landslide"
    INFRASTRUCTURE = "infrastructure"
    OTHER = "other"


class ScoringProvider(str, Enum):
    """Which system produced the classification/score."""
    LOCAL_GRANITE = "local_granite"
    RULE_BASED = "rule_based"


class FallbackState(str, Enum):
    """Operational state of the AI pipeline."""
    NORMAL = "NORMAL"
    RULE_BASED = "RULE_BASED"
    AI_BACKFILL_PENDING = "AI_BACKFILL_PENDING"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"


# ── Controlled classification reason-code vocabulary ──────────────────────────

class ClassificationReasonCode(str, Enum):
    """Controlled vocabulary for classification explanations."""
    FLOOD_WATER_DETECTED = "FLOOD_WATER_DETECTED"
    WATER_RISING = "WATER_RISING"
    BUILDING_COLLAPSED = "BUILDING_COLLAPSED"
    STRUCTURAL_DAMAGE = "STRUCTURAL_DAMAGE"
    PERSONS_TRAPPED = "PERSONS_TRAPPED"
    UNABLE_TO_EVACUATE = "UNABLE_TO_EVACUATE"
    FIRE_DETECTED = "FIRE_DETECTED"
    SMOKE_DETECTED = "SMOKE_DETECTED"
    MEDICAL_EMERGENCY = "MEDICAL_EMERGENCY"
    INJURY_REPORTED = "INJURY_REPORTED"
    UNCONSCIOUS_PERSON = "UNCONSCIOUS_PERSON"
    LANDSLIDE_DETECTED = "LANDSLIDE_DETECTED"
    SLOPE_COLLAPSE = "SLOPE_COLLAPSE"
    INFRASTRUCTURE_DAMAGE = "INFRASTRUCTURE_DAMAGE"
    ROAD_BLOCKED = "ROAD_BLOCKED"
    POWER_FAILURE = "POWER_FAILURE"
    GENERIC_INCIDENT = "GENERIC_INCIDENT"
    AI_CLASSIFIED = "AI_CLASSIFIED"


class ClassificationResult(BaseModel):
    """Output of the incident classifier."""
    incident_type: IncidentType
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0–1")
    reason_codes: List[ClassificationReasonCode] = Field(default_factory=list)
    provider: ScoringProvider
    fallback_state: FallbackState = FallbackState.NORMAL
