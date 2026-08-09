"""
Severity enums and schemas for RescueNet AI.
Implements FR-7 (0–100 score) and FR-8 (reason codes).
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SeverityLabel(str, Enum):
    """Severity tiers derived from score."""
    CRITICAL = "CRITICAL"   # 80–100
    HIGH = "HIGH"           # 60–79
    MEDIUM = "MEDIUM"       # 40–59
    LOW = "LOW"             # 0–39


# ── Controlled severity reason-code vocabulary ────────────────────────────────

class SeverityReasonCode(str, Enum):
    """Controlled vocabulary for severity explanations (FR-8)."""
    MULTIPLE_PEOPLE_AT_RISK = "MULTIPLE_PEOPLE_AT_RISK"
    PEOPLE_AT_RISK = "PEOPLE_AT_RISK"
    TRAPPED_PERSONS = "TRAPPED_PERSONS"
    MEDICAL_EMERGENCY = "MEDICAL_EMERGENCY"
    RAPIDLY_RISING_WATER = "RAPIDLY_RISING_WATER"
    FLOODING = "FLOODING"
    STRUCTURAL_DAMAGE = "STRUCTURAL_DAMAGE"
    FIRE_PRESENT = "FIRE_PRESENT"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    VULNERABLE_PEOPLE = "VULNERABLE_PEOPLE"
    ENVIRONMENTAL_DANGER = "ENVIRONMENTAL_DANGER"
    EVACUATION_IMPOSSIBLE = "EVACUATION_IMPOSSIBLE"
    INSUFFICIENT_RISK_INFORMATION = "INSUFFICIENT_RISK_INFORMATION"


SEVERITY_REASON_DESCRIPTIONS: dict[str, str] = {
    SeverityReasonCode.MULTIPLE_PEOPLE_AT_RISK: "Multiple people are potentially affected.",
    SeverityReasonCode.PEOPLE_AT_RISK: "At least one person is potentially at risk.",
    SeverityReasonCode.TRAPPED_PERSONS: "People are reported to be unable to evacuate independently.",
    SeverityReasonCode.MEDICAL_EMERGENCY: "A medical emergency or injury was reported.",
    SeverityReasonCode.RAPIDLY_RISING_WATER: "Rapidly increasing flood water indicates escalating danger.",
    SeverityReasonCode.FLOODING: "Flood conditions detected.",
    SeverityReasonCode.STRUCTURAL_DAMAGE: "Damage to a building or structure increases risk.",
    SeverityReasonCode.FIRE_PRESENT: "Active fire or flames were detected.",
    SeverityReasonCode.INFRASTRUCTURE_FAILURE: "Critical infrastructure is damaged or non-functional.",
    SeverityReasonCode.VULNERABLE_PEOPLE: "Vulnerable individuals (elderly, children, disabled) may be present.",
    SeverityReasonCode.ENVIRONMENTAL_DANGER: "Environmental conditions pose additional risk.",
    SeverityReasonCode.EVACUATION_IMPOSSIBLE: "Evacuation is reported as impossible or blocked.",
    SeverityReasonCode.INSUFFICIENT_RISK_INFORMATION: "Insufficient information to determine specific risk factors.",
}


class RiskFactors(BaseModel):
    """Structured risk factors extracted from incident text."""
    people_at_risk: int = Field(default=0, ge=0, description="Number of people reportedly at risk (0 = unknown/none)")
    trapped_persons: bool = False
    medical_emergency: bool = False
    rapidly_rising_water: bool = False
    structural_damage: bool = False
    fire_present: bool = False
    infrastructure_failure: bool = False
    evacuation_impossible: bool = False
    vulnerable_people: bool = False
    environmental_danger: bool = False


class SeverityResult(BaseModel):
    """Output of the severity engine (FR-7 + FR-8)."""
    severity_score: int = Field(..., ge=0, le=100, description="Severity 0–100")
    severity_label: SeverityLabel
    severity_reason_codes: List[SeverityReasonCode] = Field(
        ...,
        min_length=1,
        description="At least one reason code is required (FR-8)",
    )
    risk_factors: RiskFactors
