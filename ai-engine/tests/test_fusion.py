"""
Tests for FusionEngine — Phase 4.
"""

import pytest

from ai_engine.fusion.fusion_engine import FusionEngine
from ai_engine.fusion.schemas import FusedResult
from ai_engine.vision.schemas import (
    ImageAnalysisResult,
    MultimodalMode,
    VisualSignals,
)
from shared.schemas.severity import SeverityLabel, SeverityReasonCode


def _make_image_result(
    vision_available: bool = True,
    flood_water: bool = False,
    fire_present: bool = False,
    confidence: float = 0.8,
) -> ImageAnalysisResult:
    signals = VisualSignals(
        flood_water=flood_water,
        fire_present=fire_present,
        visual_confidence=confidence,
    )
    from ai_engine.vision.schemas import VisualReasonCode
    codes = []
    if flood_water:
        codes.append(VisualReasonCode.VISUAL_FLOOD_WATER)
    if fire_present:
        codes.append(VisualReasonCode.FIRE_VISIBLE)
    return ImageAnalysisResult(
        vision_available=vision_available,
        visual_signals=signals,
        visual_reason_codes=codes,
    )


class TestFusionEngine:
    def test_text_only_mode_when_no_image(self):
        engine = FusionEngine()
        result = engine.fuse(
            text_severity_score=70,
            text_reason_codes=[SeverityReasonCode.FLOODING],
            image_analysis=None,
        )
        assert result.mode == MultimodalMode.TEXT_ONLY
        assert result.severity_score == 70
        assert result.text_weight_applied == 1.0
        assert result.image_weight_applied == 0.0

    def test_text_only_fallback_when_vision_unavailable(self):
        engine = FusionEngine()
        img = ImageAnalysisResult.unavailable("no model")
        result = engine.fuse(
            text_severity_score=60,
            text_reason_codes=[SeverityReasonCode.FIRE_PRESENT],
            image_analysis=img,
        )
        assert result.mode == MultimodalMode.TEXT_ONLY_FALLBACK
        assert result.severity_score == 60

    def test_text_and_image_mode_when_vision_available(self):
        engine = FusionEngine(text_weight=0.6, image_weight=0.4)
        img = _make_image_result(vision_available=True, flood_water=True, confidence=0.9)
        result = engine.fuse(
            text_severity_score=70,
            text_reason_codes=[SeverityReasonCode.FLOODING],
            image_analysis=img,
        )
        assert result.mode == MultimodalMode.TEXT_AND_IMAGE
        assert result.image_severity_score is not None
        # Combined score should be a weighted blend
        assert 0 <= result.severity_score <= 100

    def test_severity_reason_codes_never_empty(self):
        engine = FusionEngine()
        result = engine.fuse(
            text_severity_score=30,
            text_reason_codes=[SeverityReasonCode.FLOODING],
            image_analysis=None,
        )
        assert len(result.severity_reason_codes) >= 1

    def test_label_matches_score(self):
        engine = FusionEngine()
        result = engine.fuse(
            text_severity_score=85,
            text_reason_codes=[SeverityReasonCode.TRAPPED_PERSONS],
            image_analysis=None,
        )
        assert result.severity_label == SeverityLabel.CRITICAL

    def test_score_clamped_to_100(self):
        engine = FusionEngine()
        result = engine.fuse(
            text_severity_score=100,
            text_reason_codes=[SeverityReasonCode.FLOODING],
            image_analysis=None,
        )
        assert result.severity_score <= 100

    def test_score_clamped_to_0(self):
        engine = FusionEngine()
        result = engine.fuse(
            text_severity_score=0,
            text_reason_codes=[SeverityReasonCode.INSUFFICIENT_RISK_INFORMATION],
            image_analysis=None,
        )
        assert result.severity_score >= 0

    def test_visual_reason_codes_included(self):
        engine = FusionEngine()
        from ai_engine.vision.schemas import VisualReasonCode
        img = _make_image_result(vision_available=True, fire_present=True, confidence=0.8)
        result = engine.fuse(
            text_severity_score=50,
            text_reason_codes=[SeverityReasonCode.FLOODING],
            image_analysis=img,
        )
        assert VisualReasonCode.FIRE_VISIBLE in result.visual_reason_codes
