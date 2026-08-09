"""
Fusion schemas for Phase 4 — result of text + image fusion.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from ai_engine.vision.schemas import MultimodalMode, VisualReasonCode
from shared.schemas.severity import SeverityLabel, SeverityReasonCode


class FusedResult(BaseModel):
    """
    Combined result from text analysis and image analysis fusion.
    When image is None, mode is TEXT_ONLY and full text weight applies.
    """
    severity_score: int = Field(..., ge=0, le=100)
    severity_label: SeverityLabel
    mode: MultimodalMode
    text_severity_score: int = Field(..., ge=0, le=100)
    image_severity_score: Optional[int] = Field(default=None, ge=0, le=100)
    severity_reason_codes: List[SeverityReasonCode] = Field(
        ..., min_length=1, description="Combined severity reason codes"
    )
    visual_reason_codes: List[VisualReasonCode] = Field(default_factory=list)
    text_weight_applied: float = Field(..., ge=0.0, le=1.0)
    image_weight_applied: float = Field(..., ge=0.0, le=1.0)
