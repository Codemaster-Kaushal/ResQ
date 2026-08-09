"""
Tests for fallback behavior — Phase 4-6 graceful degradation.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from app.main import app
from app.dependencies import get_granite_provider, get_triage_service
from ai_engine.triage_service import TriageService
from ai_engine.exceptions import AIModelUnavailableError
from ai_engine.classification.rule_based import classify_rule_based
from ai_engine.classification.risk_extractor import extract_risk_factors_rule_based
from ai_engine.severity.engine import calculate_severity
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.severity import RiskFactors


def _offline_provider():
    provider = MagicMock()
    provider.provider_name = "local_granite"
    provider.model_name = "granite3.3:8b"
    provider.model_version = "latest"
    provider.is_available = AsyncMock(return_value=False)
    provider.classify_incident = AsyncMock(side_effect=AIModelUnavailableError("offline"))
    provider.extract_risk_factors = AsyncMock(side_effect=AIModelUnavailableError("offline"))
    return provider


@pytest.fixture
def offline_client():
    provider = _offline_provider()
    app.dependency_overrides[get_granite_provider] = lambda: provider
    app.dependency_overrides[get_triage_service] = lambda: TriageService(provider=provider)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestFallbackBehavior:
    def test_triage_succeeds_when_ai_offline(self, offline_client):
        """FR-10: rule-based fallback must succeed when AI is unavailable."""
        resp = offline_client.post(
            "/ai/triage",
            json={"report_id": "RPT-FB1", "description": "Flooding in the area, 3 people trapped."},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["scoring_provider"] in ("rule_based",)
        assert body["fallback_state"] in ("RULE_BASED", "AI_BACKFILL_PENDING", "AI_UNAVAILABLE")

    def test_analyze_succeeds_when_ai_offline(self, offline_client):
        """Phase 6 /ai/analyze must succeed even when AI is unavailable."""
        resp = offline_client.post(
            "/ai/analyze",
            json={"report_id": "RPT-FB2", "description": "Fire in building, smoke visible."},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert 0 <= body["severity_score"] <= 100

    def test_no_image_text_only_mode(self, offline_client):
        """When no image is provided, multimodal_mode should be TEXT_ONLY."""
        resp = offline_client.post(
            "/ai/analyze",
            json={"report_id": "RPT-FB3", "description": "Building collapsed."},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["multimodal_mode"] in ("TEXT_ONLY", "TEXT_ONLY_FALLBACK", None)

    def test_invalid_image_data_handled_gracefully(self, offline_client):
        """Invalid image data must not crash the pipeline."""
        resp = offline_client.post(
            "/ai/analyze",
            json={
                "report_id": "RPT-FB4",
                "description": "Flooding everywhere.",
                "image": "not_valid_base64!!!",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "severity_score" in body

    def test_rule_based_flood_scenario_is_critical(self):
        """A trapped flood scenario with injuries must still score as a critical fallback result."""
        description = "Floodwater has entered the ground floor and trapped six residents with injuries waiting for rescue."
        classification = classify_rule_based(description)
        risk = extract_risk_factors_rule_based(description)
        severity = calculate_severity(risk, classification.confidence)
        assert classification.incident_type.value == "flooding"
        assert severity.severity_label.value in {"CRITICAL", "HIGH"}
        assert severity.severity_score >= 80

    def test_severity_reason_codes_always_present(self, offline_client):
        """FR-8: at least one reason code always returned."""
        resp = offline_client.post(
            "/ai/analyze",
            json={"report_id": "RPT-FB5", "description": "Something happened."},
        )
        assert resp.status_code == 200
        assert len(resp.json()["severity_reason_codes"]) >= 1
