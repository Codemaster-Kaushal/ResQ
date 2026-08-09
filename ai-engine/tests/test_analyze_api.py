"""
Tests for the /ai/analyze endpoint — Phase 6.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import (
    get_granite_provider,
    get_triage_pipeline,
    get_triage_service,
)
from ai_engine.pipeline import TriagePipeline
from ai_engine.triage_service import TriageService
from ai_engine.vision.schemas import ImageAnalysisResult
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.severity import RiskFactors


def _mock_text_provider():
    """Mock Granite text provider — returns flood classification."""
    provider = MagicMock()
    provider.provider_name = "local_granite"
    provider.model_name = "granite3.3:8b"
    provider.model_version = "latest"
    provider.is_available = AsyncMock(return_value=False)
    provider.classify_incident = AsyncMock(
        return_value=ClassificationResult(
            incident_type=IncidentType.FLOODING,
            confidence=0.9,
            reason_codes=[ClassificationReasonCode.FLOOD_WATER_DETECTED],
            provider=ScoringProvider.LOCAL_GRANITE,
            fallback_state=FallbackState.NORMAL,
        )
    )
    provider.extract_risk_factors = AsyncMock(
        return_value=RiskFactors(people_at_risk=5, trapped_persons=True)
    )
    return provider


def _mock_vision_provider():
    """Mock vision provider — instantly returns VISION_UNAVAILABLE (no real inference)."""
    vision = MagicMock()
    vision.provider_name = "mock_vision"
    vision.model_name = "none"
    vision.is_available = AsyncMock(return_value=False)
    vision.analyze_image = AsyncMock(
        return_value=ImageAnalysisResult.unavailable("Vision mocked out in tests.")
    )
    return vision


@pytest.fixture
def client():
    mock_prov = _mock_text_provider()
    mock_vision = _mock_vision_provider()
    # Pipeline must be overridden so /ai/analyze doesn't spin up a real GraniteVisionProvider
    mock_pipeline = TriagePipeline(provider=mock_prov, vision_provider=mock_vision)
    app.dependency_overrides[get_granite_provider] = lambda: mock_prov
    app.dependency_overrides[get_triage_service] = lambda: TriageService(provider=mock_prov)
    app.dependency_overrides[get_triage_pipeline] = lambda: mock_pipeline
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestAnalyzeEndpoint:
    def _payload(self, image=None):
        return {
            "report_id": "RPT-ANA-001",
            "description": "Five people trapped in flooded building.",
            "image": image,
            "latitude": 12.9716,
            "longitude": 77.5946,
            "client_timestamp": "2026-08-08T18:30:00Z",
            "reporter_pseudonym": "USER-ANA-1",
        }

    def test_analyze_returns_full_result(self, client):
        resp = client.post("/ai/analyze", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["report_id"] == "RPT-ANA-001"
        assert "incident_type" in body
        assert "severity" in body
        assert "score" in body["severity"]
        assert "band" in body["severity"]
        assert "reason_codes" in body["severity"]
        assert "verification_status" in body

    def test_analyze_includes_authenticity(self, client):
        resp = client.post("/ai/analyze", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert "authenticity" in body
        assert "score" in body["authenticity"]
        assert "reason_codes" in body["authenticity"]
        assert 0 <= body["authenticity"]["score"] <= 100

    def test_analyze_no_image_text_only_mode(self, client):
        resp = client.post("/ai/analyze", json=self._payload(image=None))
        assert resp.status_code == 200
        body = resp.json()
        assert "provenance" in body
        assert body["provenance"]["vision_provider"] == "none"

    def test_analyze_pipeline_version(self, client):
        resp = client.post("/ai/analyze", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert "provenance" in body
        assert "triage_provider" in body["provenance"]

    def test_analyze_empty_report_id_fails(self, client):
        payload = self._payload()
        payload["report_id"] = ""
        resp = client.post("/ai/analyze", json=payload)
        assert resp.status_code == 422

    def test_analyze_empty_description_fails(self, client):
        payload = self._payload()
        payload["description"] = ""
        resp = client.post("/ai/analyze", json=payload)
        assert resp.status_code == 422

    def test_analyze_severity_codes_never_empty(self, client):
        resp = client.post("/ai/analyze", json=self._payload())
        assert resp.status_code == 200
        assert len(resp.json()["severity"]["reason_codes"]) >= 1

    def test_authenticity_endpoint(self, client):
        resp = client.post("/ai/authenticity", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert "authenticity_score" in body
        assert "verification_status" in body
        assert "review_required" in body

    def test_analyze_backward_compatible_with_triage(self, client):
        """analyze endpoint fields overlap with triage — basic compatibility check."""
        triage_resp = client.post("/ai/triage", json=self._payload())
        analyze_resp = client.post("/ai/analyze", json=self._payload())
        assert triage_resp.status_code == 200
        assert analyze_resp.status_code == 200
        t = triage_resp.json()
        a = analyze_resp.json()
        # Core fields must match
        assert t["report_id"] == a["report_id"]
        assert t["incident_type"] == a["incident_type"]
