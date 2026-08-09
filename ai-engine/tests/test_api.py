"""
FastAPI API endpoint tests — uses TestClient (no Ollama needed).
Mocks the GraniteLocalProvider to test API layer in isolation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_granite_provider, get_triage_service
from ai_engine.exceptions import AIModelUnavailableError
from ai_engine.triage_service import TriageService
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.incident_ai import IncidentAIInput, IncidentAIOutput
from shared.schemas.severity import RiskFactors, SeverityLabel, SeverityReasonCode


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _mock_provider(available: bool = True):
    provider = MagicMock()
    provider.provider_name = "local_granite"
    provider.model_name = "granite3.3:8b"
    provider.model_version = "latest"
    provider.is_available = AsyncMock(return_value=available)
    provider.classify_incident = AsyncMock(
        return_value=ClassificationResult(
            incident_type=IncidentType.FLOODING,
            confidence=0.94,
            reason_codes=[ClassificationReasonCode.FLOOD_WATER_DETECTED],
            provider=ScoringProvider.LOCAL_GRANITE,
            fallback_state=FallbackState.NORMAL,
        )
    )
    provider.extract_risk_factors = AsyncMock(
        return_value=RiskFactors(
            people_at_risk=5,
            trapped_persons=True,
            medical_emergency=True,
            rapidly_rising_water=True,
        )
    )
    return provider


@pytest.fixture
def client():
    """TestClient with mocked Granite provider."""
    mock_prov = _mock_provider()

    app.dependency_overrides[get_granite_provider] = lambda: mock_prov
    app.dependency_overrides[get_triage_service] = lambda: TriageService(provider=mock_prov)

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def client_no_ai():
    """TestClient where Granite is unavailable (AI_PROVIDER_UNAVAILABLE scenario)."""
    mock_prov = _mock_provider(available=False)
    mock_prov.classify_incident = AsyncMock(side_effect=AIModelUnavailableError("offline"))
    mock_prov.extract_risk_factors = AsyncMock(side_effect=AIModelUnavailableError("offline"))

    app.dependency_overrides[get_granite_provider] = lambda: mock_prov
    app.dependency_overrides[get_triage_service] = lambda: TriageService(provider=mock_prov)

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


# ── GET /health ───────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_health_ai_ok(self, client):
        resp = client.get("/health/ai")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["provider"] == "local_granite"
        assert body["offline_capable"] is True

    def test_health_ai_degraded_when_unavailable(self, client_no_ai):
        resp = client_no_ai.get("/health/ai")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["error_code"] == "AI_PROVIDER_UNAVAILABLE"
        assert body["offline_capable"] is True


# ── POST /ai/classify ─────────────────────────────────────────────────────────

class TestClassifyEndpoint:
    def test_classify_flood(self, client):
        resp = client.post(
            "/ai/classify",
            json={"report_id": "RPT-001", "description": "Our house is flooded"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["report_id"] == "RPT-001"
        assert body["incident_type"] == "flooding"
        assert 0.0 <= body["confidence"] <= 1.0
        assert body["provider"] == "local_granite"
        assert body["fallback_state"] == "NORMAL"

    def test_classify_empty_report_id(self, client):
        resp = client.post(
            "/ai/classify",
            json={"report_id": "", "description": "flood"},
        )
        assert resp.status_code == 422

    def test_classify_empty_description(self, client):
        resp = client.post(
            "/ai/classify",
            json={"report_id": "RPT-001", "description": ""},
        )
        assert resp.status_code == 422

    def test_classify_missing_description(self, client):
        resp = client.post("/ai/classify", json={"report_id": "RPT-001"})
        assert resp.status_code == 422

    def test_classify_fallback_when_ai_unavailable(self, client_no_ai):
        resp = client_no_ai.post(
            "/ai/classify",
            json={"report_id": "RPT-002", "description": "flames and fire everywhere"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Must still classify via rule-based
        assert body["incident_type"] == "fire"
        assert body["provider"] == "rule_based"


# ── POST /ai/triage ───────────────────────────────────────────────────────────

class TestTriageEndpoint:
    def _flood_payload(self, image=None):
        return {
            "report_id": "RPT-001",
            "description": "Five people are trapped and one is injured in a flooded house",
            "image": image,
            "latitude": 12.9716,
            "longitude": 77.5946,
            "client_timestamp": "2026-08-08T18:30:00Z",
            "reporter_pseudonym": "USER-A7F2",
        }

    def test_triage_returns_full_output(self, client):
        resp = client.post("/ai/triage", json=self._flood_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["report_id"] == "RPT-001"
        assert body["incident_type"] == "flooding"
        assert 0.0 <= body["classification_confidence"] <= 1.0
        assert 0 <= body["severity_score"] <= 100
        assert body["severity_label"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        assert len(body["severity_reason_codes"]) >= 1
        assert body["scoring_provider"] in ("local_granite", "rule_based")

    def test_triage_with_null_image(self, client):
        """FR-9: null image must succeed."""
        resp = client.post("/ai/triage", json=self._flood_payload(image=None))
        assert resp.status_code == 200

    def test_triage_empty_report_id_fails(self, client):
        payload = self._flood_payload()
        payload["report_id"] = ""
        resp = client.post("/ai/triage", json=payload)
        assert resp.status_code == 422

    def test_triage_empty_description_fails(self, client):
        payload = self._flood_payload()
        payload["description"] = ""
        resp = client.post("/ai/triage", json=payload)
        assert resp.status_code == 422

    def test_triage_invalid_latitude(self, client):
        payload = self._flood_payload()
        payload["latitude"] = 200.0
        resp = client.post("/ai/triage", json=payload)
        assert resp.status_code == 422

    def test_triage_fallback_when_ai_unavailable(self, client_no_ai):
        resp = client_no_ai.post("/ai/triage", json=self._flood_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["scoring_provider"] == "rule_based"
        assert body["fallback_state"] in ("RULE_BASED", "AI_BACKFILL_PENDING")

    def test_triage_medical_report(self, client_no_ai):
        payload = {
            "report_id": "RPT-MED",
            "description": "My father is unconscious and bleeding.",
        }
        resp = client_no_ai.post("/ai/triage", json=payload)
        assert resp.status_code == 200

    def test_triage_fire_report(self, client_no_ai):
        payload = {"report_id": "RPT-FIRE", "description": "Flames and smoke from the building."}
        resp = client_no_ai.post("/ai/triage", json=payload)
        assert resp.status_code == 200

    def test_triage_structural_collapse(self, client_no_ai):
        payload = {"report_id": "RPT-SC", "description": "The building collapsed on residents."}
        resp = client_no_ai.post("/ai/triage", json=payload)
        assert resp.status_code == 200


# ── POST /ai/severity ─────────────────────────────────────────────────────────

class TestSeverityEndpoint:
    def test_severity_returns_score(self, client):
        resp = client.post(
            "/ai/severity",
            json={"report_id": "RPT-001", "description": "5 people trapped in flooded house"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert 0 <= body["severity_score"] <= 100
        assert len(body["severity_reason_codes"]) >= 1

    def test_severity_with_null_image(self, client):
        resp = client.post(
            "/ai/severity",
            json={"report_id": "RPT-002", "description": "fire in building", "image": None},
        )
        assert resp.status_code == 200

    def test_severity_reason_codes_never_empty(self, client):
        resp = client.post(
            "/ai/severity",
            json={"report_id": "RPT-003", "description": "unusual activity"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["severity_reason_codes"]) >= 1


# ── GET /ai/provenance ────────────────────────────────────────────────────────

class TestProvenanceEndpoint:
    def test_provenance_structure(self, client):
        resp = client.get("/ai/provenance")
        assert resp.status_code == 200
        body = resp.json()
        assert "triage_provider" in body
        assert "model" in body
        assert "model_version" in body
        assert "scoring_provider" in body
        assert body["scoring_provider"] == "hybrid"
        assert body["fallback_enabled"] is True
        assert body["offline_capable"] is True
        assert "thresholds" in body
        assert "critical" in body["thresholds"]
        assert "high" in body["thresholds"]
        assert "medium" in body["thresholds"]


# ── Structured error format (NFR-5) ──────────────────────────────────────────

class TestStructuredErrors:
    def test_invalid_json_body_returns_422(self, client):
        resp = client.post("/ai/classify", content=b"not json", headers={"content-type": "application/json"})
        assert resp.status_code == 422

    def test_422_does_not_expose_stack_trace(self, client):
        resp = client.post("/ai/classify", json={"report_id": "", "description": ""})
        body = resp.text
        assert "Traceback" not in body
        assert "File " not in body
