"""
Tests for the provenance endpoint — Phase 6.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_granite_provider, get_triage_service
from ai_engine.triage_service import TriageService
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.severity import RiskFactors


def _mock_provider():
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
    provider.extract_risk_factors = AsyncMock(return_value=RiskFactors())
    return provider


@pytest.fixture
def client():
    mock_prov = _mock_provider()
    app.dependency_overrides[get_granite_provider] = lambda: mock_prov
    app.dependency_overrides[get_triage_service] = lambda: TriageService(provider=mock_prov)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestProvenanceEndpointPhase6:
    def test_provenance_has_basic_fields(self, client):
        resp = client.get("/ai/provenance")
        assert resp.status_code == 200
        body = resp.json()
        assert "triage_provider" in body
        assert "model" in body
        assert "model_version" in body
        assert "scoring_provider" in body
        assert body["fallback_enabled"] is True
        assert body["offline_capable"] is True

    def test_provenance_has_vision_fields(self, client):
        resp = client.get("/ai/provenance")
        body = resp.json()
        assert "vision_provider" in body
        assert "vision_available" in body
        assert isinstance(body["vision_available"], bool)

    def test_provenance_has_thresholds(self, client):
        resp = client.get("/ai/provenance")
        body = resp.json()
        assert "thresholds" in body
        thresholds = body["thresholds"]
        assert "critical" in thresholds
        assert "high" in thresholds
        assert "medium" in thresholds
        assert "authenticity_verified" in thresholds
        assert "authenticity_likely_valid" in thresholds
        assert "authenticity_review" in thresholds

    def test_provenance_has_fusion(self, client):
        resp = client.get("/ai/provenance")
        body = resp.json()
        assert "fusion" in body
        assert "text_weight" in body["fusion"]
        assert "image_weight" in body["fusion"]
        total = body["fusion"]["text_weight"] + body["fusion"]["image_weight"]
        assert total == pytest.approx(1.0, abs=0.01)

    def test_provenance_has_authenticity_config(self, client):
        resp = client.get("/ai/provenance")
        body = resp.json()
        assert "authenticity" in body
        auth = body["authenticity"]
        assert "corroboration_radius_meters" in auth
        assert "max_clock_skew_minutes" in auth

    def test_provenance_has_pipeline_version(self, client):
        resp = client.get("/ai/provenance")
        body = resp.json()
        assert "pipeline_version" in body
        assert body["pipeline_version"] == "4.0"

    def test_provenance_has_phases(self, client):
        resp = client.get("/ai/provenance")
        body = resp.json()
        assert "phases" in body
        assert "4" in body["phases"]
        assert "5" in body["phases"]
        assert "6" in body["phases"]
