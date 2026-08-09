"""
Core AI input/output contract for RescueNet AI.
This is the integration boundary between Person 1 (AI engine) and Person 2 (backend).
Extended in Phases 4-5 with optional multimodal and authenticity fields.
All new fields are Optional with defaults for full backward compatibility.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from shared.schemas.classification import (
    ClassificationReasonCode,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.severity import RiskFactors, SeverityLabel, SeverityReasonCode


# ── Input ─────────────────────────────────────────────────────────────────────

class IncidentAIInput(BaseModel):
    """
    AI engine input — submitted by a citizen or field volunteer.
    Person 2 constructs this from the incoming report and sends it to the AI engine.
    FR-9: image is optional (text-only mode must succeed).
    """
    report_id: str = Field(..., description="Unique report identifier")
    description: str = Field(..., description="Free-text incident description")
    image: Optional[str] = Field(
        default=None,
        description="Base64-encoded image or URL — optional (FR-9)",
    )
    latitude: Optional[float] = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="Latitude -90 to 90",
    )
    longitude: Optional[float] = Field(
        default=None,
        ge=-180.0,
        le=180.0,
        description="Longitude -180 to 180",
    )
    client_timestamp: Optional[datetime] = Field(
        default=None,
        description="Client-side timestamp in ISO 8601",
    )
    reporter_pseudonym: Optional[str] = Field(
        default=None,
        description="Pseudonymous reporter identifier — never a real name",
    )

    @field_validator("report_id")
    @classmethod
    def report_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("report_id must not be empty")
        return v.strip()

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description must not be empty")
        return v.strip()

    model_config = {
        "json_schema_extra": {
            "example": {
                "report_id": "RPT-001",
                "description": "Four people are trapped inside a flooded house",
                "image": None,
                "latitude": 12.9716,
                "longitude": 77.5946,
                "client_timestamp": "2026-08-08T18:30:00Z",
                "reporter_pseudonym": "USER-A7F2",
            }
        }
    }


# ── Output ────────────────────────────────────────────────────────────────────

class IncidentAIOutput(BaseModel):
    """
    AI engine output — Phases 0–5.
    Designed to be extended in later phases without breaking Person 2's integration.
    All Phase 4-5 fields are Optional with defaults for backward compatibility.
    """
    report_id: str
    incident_type: IncidentType
    classification_confidence: float = Field(..., ge=0.0, le=1.0)
    classification_reason_codes: List[ClassificationReasonCode] = Field(default_factory=list)

    risk_factors: RiskFactors

    severity_score: int = Field(..., ge=0, le=100)
    severity_label: SeverityLabel
    severity_reason_codes: List[SeverityReasonCode] = Field(
        ...,
        min_length=1,
        description="At least one code is mandatory (FR-8)",
    )

    scoring_provider: ScoringProvider
    fallback_state: FallbackState

    # ── Phase 4: Multimodal ───────────────────────────────────────────────────
    image_analysis: Optional["ImageAnalysisResult"] = Field(
        default=None,
        description="Image analysis result (Phase 4). None when no image provided.",
    )
    multimodal_mode: Optional["MultimodalMode"] = Field(
        default=None,
        description="How text and image were combined (Phase 4).",
    )

    # ── Phase 5: Authenticity ─────────────────────────────────────────────────
    authenticity: Optional["AuthenticityResult"] = Field(
        default=None,
        description="Authenticity check result (Phase 5). None if not run.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "report_id": "RPT-001",
                "incident_type": "flooding",
                "classification_confidence": 0.94,
                "classification_reason_codes": ["FLOOD_WATER_DETECTED", "WATER_RISING"],
                "risk_factors": {
                    "people_at_risk": 4,
                    "trapped_persons": True,
                    "medical_emergency": False,
                    "rapidly_rising_water": False,
                    "structural_damage": False,
                    "fire_present": False,
                    "infrastructure_failure": False,
                    "evacuation_impossible": False,
                    "vulnerable_people": False,
                    "environmental_danger": False,
                },
                "severity_score": 88,
                "severity_label": "CRITICAL",
                "severity_reason_codes": ["PEOPLE_AT_RISK", "TRAPPED_PERSONS", "FLOODING"],
                "scoring_provider": "local_granite",
                "fallback_state": "NORMAL",
                "image_analysis": None,
                "multimodal_mode": None,
                "authenticity": None,
            }
        }
    }


# ── Full analysis result (Phase 6 /ai/analyze) ────────────────────────────────

class FullAnalysisResult(BaseModel):
    """
    Complete output from the /ai/analyze endpoint (Phase 6).
    Wraps all pipeline outputs in named sections for clarity.
    """
    report_id: str
    # Classification section
    incident_type: IncidentType
    classification_confidence: float = Field(..., ge=0.0, le=1.0)
    classification_reason_codes: List[ClassificationReasonCode] = Field(default_factory=list)
    # Risk + severity section
    risk_factors: RiskFactors
    severity_score: int = Field(..., ge=0, le=100)
    severity_label: SeverityLabel
    severity_reason_codes: List[SeverityReasonCode] = Field(default_factory=list)
    # Provider state
    scoring_provider: ScoringProvider
    fallback_state: FallbackState
    # Multimodal section (Phase 4)
    image_analysis: Optional["ImageAnalysisResult"] = None
    multimodal_mode: Optional["MultimodalMode"] = None
    # Authenticity section (Phase 5)
    authenticity: Optional["AuthenticityResult"] = None
    # Pipeline metadata
    pipeline_version: str = "4.0"


# ── Structured error wrapper (NFR-5) ──────────────────────────────────────────

class AIErrorDetail(BaseModel):
    """Structured error body — never expose raw stack traces."""
    code: str
    message: str
    fallback: Optional[str] = None
    retryable: bool = False


class AIErrorResponse(BaseModel):
    error: AIErrorDetail


# ── Forward reference resolution ──────────────────────────────────────────────
# Import here to avoid circular imports at module load time

def _resolve_forward_refs() -> None:
    try:
        from ai_engine.vision.schemas import ImageAnalysisResult, MultimodalMode
        from ai_engine.authenticity.schemas import AuthenticityResult
        IncidentAIOutput.model_rebuild()
        FullAnalysisResult.model_rebuild()
    except Exception:
        pass  # Best-effort; Pydantic will resolve lazily


_resolve_forward_refs()
