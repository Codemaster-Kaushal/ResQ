"""
Tests for vision schemas and ImageAnalysisResult factories — Phase 4.
"""

import pytest
from ai_engine.vision.schemas import (
    ImageAnalysisResult,
    MultimodalMode,
    VisualReasonCode,
    VisualSignals,
)


class TestImageAnalysisResult:
    def test_unavailable_factory(self):
        result = ImageAnalysisResult.unavailable("No model")
        assert result.vision_available is False
        assert VisualReasonCode.VISION_UNAVAILABLE in result.visual_reason_codes
        assert result.error_message == "No model"

    def test_failed_factory(self):
        result = ImageAnalysisResult.failed("Timeout")
        assert result.vision_available is False
        assert VisualReasonCode.VISION_FAILED in result.visual_reason_codes
        assert "Timeout" in result.error_message

    def test_default_visual_signals_all_false(self):
        signals = VisualSignals()
        assert signals.flood_water is False
        assert signals.fire_present is False
        assert signals.people_visible is False
        assert signals.visual_confidence == 0.0

    def test_visual_signals_validation(self):
        signals = VisualSignals(flood_water=True, visual_confidence=0.85)
        assert signals.flood_water is True
        assert signals.visual_confidence == 0.85

    def test_multimodal_mode_values(self):
        assert MultimodalMode.TEXT_ONLY == "TEXT_ONLY"
        assert MultimodalMode.TEXT_AND_IMAGE == "TEXT_AND_IMAGE"
        assert MultimodalMode.TEXT_ONLY_FALLBACK == "TEXT_ONLY_FALLBACK"

    def test_result_model_copy(self):
        """Verify model_copy works (used in ImageAnalyzer to attach exif)."""
        from ai_engine.vision.schemas import ExifData
        result = ImageAnalysisResult.unavailable("test")
        exif = ExifData(width=100, height=100)
        updated = result.model_copy(update={"exif_data": exif})
        assert updated.exif_data is not None
        assert updated.exif_data.width == 100

    def test_vision_reason_codes_all_defined(self):
        """All enum members are accessible."""
        codes = [c for c in VisualReasonCode]
        assert len(codes) == 11
