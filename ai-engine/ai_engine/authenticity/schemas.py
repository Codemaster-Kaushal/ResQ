"""
Authenticity schemas for Phase 5 — trust and verification models.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    """Overall verification status of a report."""
    VERIFIED = "VERIFIED"           # score >= AUTHENTICITY_VERIFIED_THRESHOLD
    LIKELY_VALID = "LIKELY_VALID"   # score >= AUTHENTICITY_LIKELY_VALID_THRESHOLD
    NEEDS_REVIEW = "NEEDS_REVIEW"   # score >= AUTHENTICITY_REVIEW_THRESHOLD
    FLAGGED = "FLAGGED"             # score < AUTHENTICITY_REVIEW_THRESHOLD


class AuthenticityReasonCode(str, Enum):
    """Controlled vocabulary for authenticity explanations."""
    IMAGE_NOT_DUPLICATE = "IMAGE_NOT_DUPLICATE"
    IMAGE_DUPLICATE = "IMAGE_DUPLICATE"
    IMAGE_NEAR_DUPLICATE = "IMAGE_NEAR_DUPLICATE"
    COORDINATES_VALID = "COORDINATES_VALID"
    COORDINATES_INVALID = "COORDINATES_INVALID"
    COORDINATES_MISSING = "COORDINATES_MISSING"
    TIMESTAMP_PLAUSIBLE = "TIMESTAMP_PLAUSIBLE"
    TIMESTAMP_SKEW = "TIMESTAMP_SKEW"
    MOVEMENT_PLAUSIBLE = "MOVEMENT_PLAUSIBLE"
    IMPOSSIBLE_MOVEMENT = "IMPOSSIBLE_MOVEMENT"
    NEARBY_CORROBORATION = "NEARBY_CORROBORATION"
    NO_CORROBORATION = "NO_CORROBORATION"
    EXIF_NOT_AVAILABLE = "EXIF_NOT_AVAILABLE"
    EXIF_TIMESTAMP_MATCH = "EXIF_TIMESTAMP_MATCH"
    NO_IMAGE_SUBMITTED = "NO_IMAGE_SUBMITTED"


class AuthenticityEvidence(BaseModel):
    """Evidence collected for authenticity calculation."""
    image_duplicate: bool = False
    geo_valid: bool = True
    timestamp_plausible: bool = True
    movement_plausible: bool = True
    corroborating_reports: int = 0
    image_hash: Optional[str] = None
    exif_timestamp: Optional[datetime] = None
    exif_gps: Optional[Tuple[float, float]] = None


class AuthenticityResult(BaseModel):
    """Complete authenticity evaluation result."""
    authenticity_score: int = Field(..., ge=0, le=100, description="Authenticity score 0-100")
    verification_status: VerificationStatus
    review_required: bool
    authenticity_reason_codes: List[AuthenticityReasonCode] = Field(default_factory=list)
    evidence: AuthenticityEvidence = Field(default_factory=AuthenticityEvidence)
