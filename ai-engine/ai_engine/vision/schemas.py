"""
Vision schemas for Phase 4 — image analysis results and visual signals.
"""

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class VisualReasonCode(str, Enum):
    """Controlled vocabulary for visual analysis explanations."""
    VISUAL_FLOOD_WATER = "VISUAL_FLOOD_WATER"
    PEOPLE_VISIBLE = "PEOPLE_VISIBLE"
    STRUCTURAL_DAMAGE = "STRUCTURAL_DAMAGE"
    FIRE_VISIBLE = "FIRE_VISIBLE"
    SMOKE_VISIBLE = "SMOKE_VISIBLE"
    ROAD_BLOCKED = "ROAD_BLOCKED"
    VEHICLE_SUBMERGED = "VEHICLE_SUBMERGED"
    UNSAFE_ENVIRONMENT = "UNSAFE_ENVIRONMENT"
    VISION_UNAVAILABLE = "VISION_UNAVAILABLE"
    VISION_FAILED = "VISION_FAILED"
    LOW_VISUAL_CONFIDENCE = "LOW_VISUAL_CONFIDENCE"


class MultimodalMode(str, Enum):
    """Describes how text and image inputs were combined."""
    TEXT_ONLY = "TEXT_ONLY"
    TEXT_AND_IMAGE = "TEXT_AND_IMAGE"
    TEXT_ONLY_FALLBACK = "TEXT_ONLY_FALLBACK"


# ── Pydantic models ───────────────────────────────────────────────────────────

class ExifData(BaseModel):
    """EXIF metadata extracted from an image."""
    timestamp: Optional[datetime] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    software: Optional[str] = None


class VisualSignals(BaseModel):
    """Visual signals detected in an image."""
    flood_water: bool = False
    people_visible: bool = False
    structural_damage: bool = False
    fire_present: bool = False
    smoke_visible: bool = False
    road_blocked: bool = False
    vehicle_submerged: bool = False
    unsafe_environment: bool = False
    visual_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ImageAnalysisResult(BaseModel):
    """Complete result of image analysis."""
    vision_available: bool = False
    visual_signals: VisualSignals = Field(default_factory=VisualSignals)
    visual_reason_codes: List[VisualReasonCode] = Field(default_factory=list)
    exif_data: Optional[ExifData] = None
    provider_name: Optional[str] = None
    model_name: Optional[str] = None
    error_message: Optional[str] = None

    @classmethod
    def unavailable(cls, reason: str = "Vision provider not available") -> "ImageAnalysisResult":
        """Factory for VISION_UNAVAILABLE result."""
        return cls(
            vision_available=False,
            visual_reason_codes=[VisualReasonCode.VISION_UNAVAILABLE],
            error_message=reason,
        )

    @classmethod
    def failed(cls, reason: str) -> "ImageAnalysisResult":
        """Factory for VISION_FAILED result."""
        return cls(
            vision_available=False,
            visual_reason_codes=[VisualReasonCode.VISION_FAILED],
            error_message=reason,
        )


# ── Preprocessing dataclass ───────────────────────────────────────────────────

@dataclass
class PreprocessedImage:
    """Holds the preprocessed image data without modifying the original evidence."""
    original_bytes: bytes
    normalized_bytes: bytes
    exif_data: Optional[ExifData]
    mime_type: str
    width: int
    height: int
    file_size_bytes: int
