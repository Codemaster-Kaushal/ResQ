"""
Tests for the full TriagePipeline — Phase 6 integration.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from ai_engine.pipeline import TriagePipeline
from ai_engine.authenticity.schemas import AuthenticityResult, VerificationStatus
from ai_engine.vision.schemas import ImageAnalysisResult, MultimodalMode
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.incident_ai import FullAnalysisResult, IncidentAIInput
from shared.schemas.severity import RiskFactors


def _flood_input(image=None):
    return IncidentAIInput(
        report_id="RPT-PIPE-001",
        description="Five people trapped in flooded building, water rising fast.",
        image=image,
        latitude=12.9716,
        longitude=77.5946,
        reporter_pseudonym="USER-PIPE-1",
    )


class TestTriagePipeline:
    @pytest.mark.asyncio
    async def test_text_only_pipeline_returns_output(self):
        pipeline = TriagePipeline(provider=None)
        result = await pipeline.run(_flood_input())
        assert result.report_id == "RPT-PIPE-001"
        assert result.severity_score >= 0
        assert len(result.severity_reason_codes) >= 1
        assert result.multimodal_mode == MultimodalMode.TEXT_ONLY

    @pytest.mark.asyncio
    async def test_pipeline_includes_authenticity(self):
        pipeline = TriagePipeline(provider=None)
        result = await pipeline.run(_flood_input())
        assert result.authenticity is not None
        assert isinstance(result.authenticity, AuthenticityResult)
        assert 0 <= result.authenticity.authenticity_score <= 100

    @pytest.mark.asyncio
    async def test_pipeline_no_image_means_no_image_analysis_signals(self):
        pipeline = TriagePipeline(provider=None)
        result = await pipeline.run(_flood_input(image=None))
        # image_analysis should be None when no image provided
        assert result.image_analysis is None

    @pytest.mark.asyncio
    async def test_pipeline_run_full_returns_full_analysis_result(self):
        pipeline = TriagePipeline(provider=None)
        result = await pipeline.run_full(_flood_input())
        assert isinstance(result, FullAnalysisResult)
        assert result.pipeline_version == "4.0"
        assert result.report_id == "RPT-PIPE-001"

    @pytest.mark.asyncio
    async def test_pipeline_fallback_state_preserved(self):
        pipeline = TriagePipeline(provider=None)
        result = await pipeline.run(_flood_input())
        # Rule-based fallback state when no provider
        assert result.fallback_state == FallbackState.RULE_BASED

    @pytest.mark.asyncio
    async def test_pipeline_invalid_image_handled_gracefully(self):
        """Invalid base64 image must not crash pipeline."""
        pipeline = TriagePipeline(provider=None)
        inp = IncidentAIInput(
            report_id="RPT-BAD-IMG",
            description="Flooding in area.",
            image="not_valid_base64!!!",
        )
        result = await pipeline.run(inp)
        # Should succeed with fallback — image_analysis.vision_available=False
        assert result.severity_score >= 0
        if result.image_analysis is not None:
            assert result.image_analysis.vision_available is False

    @pytest.mark.asyncio
    async def test_pipeline_with_mock_vision(self):
        """Pipeline integrates correctly with a mocked vision provider."""
        from ai_engine.vision.schemas import VisualSignals, VisualReasonCode
        import base64
        import io
        from PIL import Image

        # Create a valid test image
        img = Image.new("RGB", (64, 64), color=(70, 130, 180))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        mock_vision = MagicMock()
        mock_vision.analyze_image = AsyncMock(
            return_value=ImageAnalysisResult(
                vision_available=True,
                visual_signals=VisualSignals(
                    flood_water=True,
                    visual_confidence=0.9,
                ),
                visual_reason_codes=[VisualReasonCode.VISUAL_FLOOD_WATER],
            )
        )

        from ai_engine.vision.image_analyzer import ImageAnalyzer
        analyzer = ImageAnalyzer(vision_provider=mock_vision)
        pipeline = TriagePipeline(provider=None, image_analyzer=analyzer)

        inp = _flood_input(image=b64)
        result = await pipeline.run(inp)
        assert result.multimodal_mode == MultimodalMode.TEXT_AND_IMAGE
        assert result.image_analysis is not None
        assert result.image_analysis.vision_available is True
