"""
Tests for the master pipeline (analyze_report) and safety override rules.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ai_engine.analyze import analyze_report
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.severity import RiskFactors
from ai_engine.vision.schemas import ImageAnalysisResult, VisualSignals
from authenticity import reset_state, _load_state

# ── Mocking Helpers ──────────────────────────────────────────────────────────

def _make_mock_provider(incident_type=IncidentType.FLOODING, confidence=0.9):
    provider = MagicMock()
    provider.provider_name = "local_granite"
    provider.model_name = "granite3.3:8b"
    provider.model_version = "latest"
    provider.is_available = AsyncMock(return_value=True)
    provider.classify_incident = AsyncMock(
        return_value=ClassificationResult(
            incident_type=incident_type,
            confidence=confidence,
            reason_codes=[ClassificationReasonCode.FLOOD_WATER_DETECTED],
            provider=ScoringProvider.LOCAL_GRANITE,
            fallback_state=FallbackState.NORMAL,
        )
    )
    return provider


def _make_mock_vision_provider(flood=True):
    vision = MagicMock()
    vision.provider_name = "mock_vision"
    vision.model_name = "gemma4:latest"
    vision.is_available = AsyncMock(return_value=True)
    vision.analyze_image = AsyncMock(
        return_value=ImageAnalysisResult(
            vision_available=True,
            visual_signals=VisualSignals(
                flood_water=flood,
                visual_confidence=0.95,
            ),
            visual_reason_codes=[],
            provider_name="mock_vision",
            model_name="gemma4:latest",
        )
    )
    return vision


@pytest.fixture(autouse=True)
def clean_state():
    """Reset state.json before and after every test."""
    reset_state()
    yield
    reset_state()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMasterPipelineOverrides:

    @pytest.mark.asyncio
    async def test_critical_severity_and_needs_review_authenticity_override(self):
        """
        If severity = CRITICAL and authenticity = NEEDS_REVIEW:
        verification_status = LIKELY_VALID
        and add CRITICAL_SEVERITY_OVERRIDE to reason_codes
        """
        mock_provider = _make_mock_provider()
        # Mock rule-based risk extractor to return high risk (CRITICAL severity)
        mock_risk = RiskFactors(
            people_at_risk=5,
            trapped_persons=True,
            medical_emergency=True,
            rapidly_rising_water=True,
        )
        
        with patch("ai_engine.analyze.extract_risk_factors_rule_based", return_value=mock_risk):
            # We want authenticity to return NEEDS_REVIEW (score 40-59).
            # By triggering geo invalid (-25), score = 70 (base) - 25 = 45 (NEEDS_REVIEW band)
            report = {
                "report_id": "RPT-OVERRIDE-1",
                "description": "5 people trapped in flooded house. Injured person.",
                "latitude": 500.0,  # Invalid GPS -> GEO_INVALID -> NEEDS_REVIEW
                "longitude": 77.5946,
                "client_timestamp": "2026-08-09T10:00:00Z",
                "reporter_pseudonym": "USER-OV-1",
            }
            
            res = await analyze_report(report, provider=mock_provider)
            
            assert res["severity"]["band"] == "CRITICAL"
            assert res["verification_status"] == "LIKELY_VALID"
            assert "CRITICAL_SEVERITY_OVERRIDE" in res["authenticity"]["reason_codes"]

    @pytest.mark.asyncio
    async def test_flagged_authenticity_stays_flagged(self):
        """
        A FLAGGED report stays FLAGGED, even with CRITICAL severity.
        """
        mock_provider = _make_mock_provider()
        mock_vision = _make_mock_vision_provider(flood=True)
        # Ensure the mock vision provider returns signals that yield a critical fused score (>= 80)
        mock_vision.analyze_image.return_value.visual_signals.fire_present = True
        
        mock_risk = RiskFactors(
            people_at_risk=10,
            trapped_persons=True,
            rapidly_rising_water=True,
        )
        
        # Reset state so we can trigger a duplicate
        reset_state()
        
        # Set up exact duplicate image path
        import base64
        import io
        from PIL import Image
        
        img = Image.new("RGB", (64, 64), color=(100, 100, 100))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        with patch("ai_engine.analyze.extract_risk_factors_rule_based", return_value=mock_risk):
            report_first = {
                "report_id": "RPT-DUP-A",
                "description": "Critical emergency.",
                "image": img_b64,
                "latitude": 12.9716,
                "longitude": 77.5946,
                "client_timestamp": "2026-08-09T10:00:00Z",
                "reporter_pseudonym": "USER-DUP-1",
            }
            # First submit stores the image hash
            await analyze_report(report_first, provider=mock_provider, vision_provider=mock_vision)
            
            # Second submit uses same image, same pseud, invalid gps -> score decreases to FLAGGED (<= 39)
            report_second = {
                "report_id": "RPT-DUP-B",
                "description": "Critical emergency.",
                "image": img_b64,
                "latitude": 500.0,  # Invalid GPS -> -25
                "longitude": 77.5946,
                "client_timestamp": "2026-08-09T10:00:00Z",
                "reporter_pseudonym": "USER-DUP-2",
            }
            
            res = await analyze_report(report_second, provider=mock_provider, vision_provider=mock_vision)
            
            # Verify it remains FLAGGED and does NOT get overridden to LIKELY_VALID
            assert res["severity"]["band"] == "CRITICAL"
            assert res["verification_status"] == "FLAGGED"
            assert "CRITICAL_SEVERITY_OVERRIDE" not in res["authenticity"]["reason_codes"]

    @pytest.mark.asyncio
    async def test_non_critical_severity_does_not_override(self):
        """
        If severity is not CRITICAL (e.g. HIGH or LOW) and authenticity is NEEDS_REVIEW,
        verification_status remains NEEDS_REVIEW.
        """
        mock_provider = _make_mock_provider()
        
        # Low risk factors -> LOW severity
        mock_risk = RiskFactors(
            people_at_risk=0,
            trapped_persons=False,
            medical_emergency=False,
        )
        
        with patch("ai_engine.analyze.extract_risk_factors_rule_based", return_value=mock_risk):
            report = {
                "report_id": "RPT-NO-OVERRIDE",
                "description": "Minor water on road.",
                "latitude": 500.0,  # Invalid GPS -> NEEDS_REVIEW
                "longitude": 77.5946,
                "client_timestamp": "2026-08-09T10:00:00Z",
                "reporter_pseudonym": "USER-NO-1",
            }
            
            res = await analyze_report(report, provider=mock_provider)
            
            assert res["severity"]["band"] != "CRITICAL"
            assert res["verification_status"] == "NEEDS_REVIEW"
            assert "CRITICAL_SEVERITY_OVERRIDE" not in res["authenticity"]["reason_codes"]

    @pytest.mark.asyncio
    async def test_state_json_persistence(self):
        """
        Verify that report metadata is correctly appended to state.json upon successful run.
        """
        mock_provider = _make_mock_provider()
        report = {
            "report_id": "RPT-PERSIST-1",
            "description": "Water rising fast in flooding incident.",
            "latitude": 12.9716,
            "longitude": 77.5946,
            "client_timestamp": "2026-08-09T10:00:00Z",
            "reporter_pseudonym": "USER-PERSIST",
        }
        
        await analyze_report(report, provider=mock_provider)
        
        # Load state.json and check that the metadata is stored
        state = _load_state()
        recent = state.get("recent_reports", [])
        
        assert len(recent) >= 1
        saved = [r for r in recent if r.get("report_id") == "RPT-PERSIST-1"]
        assert len(saved) == 1
        assert saved[0]["pseudonym"] == "USER-PERSIST"
        assert saved[0]["lat"] == 12.9716
        assert saved[0]["lon"] == 77.5946
        assert saved[0]["incident_type"] == "flooding"
