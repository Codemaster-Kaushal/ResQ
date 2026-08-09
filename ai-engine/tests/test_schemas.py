"""
Tests for Phase 0 Pydantic schemas — validation rules and contracts.
"""

import pytest
from datetime import datetime
from pydantic import ValidationError

from shared.schemas.incident_ai import IncidentAIInput, IncidentAIOutput, AIErrorDetail, AIErrorResponse
from shared.schemas.classification import (
    IncidentType,
    ScoringProvider,
    FallbackState,
    ClassificationReasonCode,
    ClassificationResult,
)
from shared.schemas.severity import (
    SeverityLabel,
    SeverityReasonCode,
    SeverityResult,
    RiskFactors,
)


# ── IncidentAIInput validation ────────────────────────────────────────────────

class TestIncidentAIInput:
    def test_valid_full_input(self):
        inp = IncidentAIInput(
            report_id="RPT-001",
            description="Four people are trapped inside a flooded house",
            image=None,
            latitude=12.9716,
            longitude=77.5946,
            client_timestamp=datetime(2026, 8, 8, 18, 30, 0),
            reporter_pseudonym="USER-A7F2",
        )
        assert inp.report_id == "RPT-001"
        assert inp.latitude == 12.9716

    def test_valid_minimal_input(self):
        """Only report_id and description are required."""
        inp = IncidentAIInput(report_id="RPT-002", description="House is on fire")
        assert inp.image is None
        assert inp.latitude is None
        assert inp.longitude is None

    def test_empty_report_id_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            IncidentAIInput(report_id="", description="Some incident")
        assert "report_id" in str(exc_info.value)

    def test_whitespace_only_report_id_raises(self):
        with pytest.raises(ValidationError):
            IncidentAIInput(report_id="   ", description="Some incident")

    def test_empty_description_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            IncidentAIInput(report_id="RPT-001", description="")
        assert "description" in str(exc_info.value)

    def test_whitespace_description_raises(self):
        with pytest.raises(ValidationError):
            IncidentAIInput(report_id="RPT-001", description="   ")

    def test_latitude_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            IncidentAIInput(report_id="RPT-001", description="flood", latitude=91.0)

    def test_latitude_below_range_raises(self):
        with pytest.raises(ValidationError):
            IncidentAIInput(report_id="RPT-001", description="flood", latitude=-91.0)

    def test_longitude_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            IncidentAIInput(report_id="RPT-001", description="flood", longitude=181.0)

    def test_longitude_below_range_raises(self):
        with pytest.raises(ValidationError):
            IncidentAIInput(report_id="RPT-001", description="flood", longitude=-181.0)

    def test_latitude_boundary_valid(self):
        inp = IncidentAIInput(report_id="RPT-001", description="flood", latitude=90.0)
        assert inp.latitude == 90.0
        inp2 = IncidentAIInput(report_id="RPT-001", description="flood", latitude=-90.0)
        assert inp2.latitude == -90.0

    def test_longitude_boundary_valid(self):
        inp = IncidentAIInput(report_id="RPT-001", description="flood", longitude=180.0)
        assert inp.longitude == 180.0

    def test_image_optional(self):
        """FR-9: image must be optional without breaking the model."""
        inp = IncidentAIInput(report_id="RPT-003", description="flooding", image=None)
        assert inp.image is None
        inp2 = IncidentAIInput(report_id="RPT-003", description="flooding", image="base64data")
        assert inp2.image == "base64data"


# ── IncidentAIOutput validation ───────────────────────────────────────────────

class TestIncidentAIOutput:
    def _valid_output(self, **overrides):
        defaults = dict(
            report_id="RPT-001",
            incident_type=IncidentType.FLOODING,
            classification_confidence=0.94,
            classification_reason_codes=[ClassificationReasonCode.FLOOD_WATER_DETECTED],
            risk_factors=RiskFactors(people_at_risk=4, trapped_persons=True),
            severity_score=88,
            severity_label=SeverityLabel.CRITICAL,
            severity_reason_codes=[SeverityReasonCode.PEOPLE_AT_RISK],
            scoring_provider=ScoringProvider.LOCAL_GRANITE,
            fallback_state=FallbackState.NORMAL,
        )
        defaults.update(overrides)
        return IncidentAIOutput(**defaults)

    def test_valid_output(self):
        out = self._valid_output()
        assert out.severity_score == 88
        assert out.severity_label == SeverityLabel.CRITICAL

    def test_severity_score_below_zero_raises(self):
        with pytest.raises(ValidationError):
            self._valid_output(severity_score=-1)

    def test_severity_score_above_100_raises(self):
        with pytest.raises(ValidationError):
            self._valid_output(severity_score=101)

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            self._valid_output(classification_confidence=-0.1)

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            self._valid_output(classification_confidence=1.1)

    def test_empty_severity_reason_codes_raises(self):
        """FR-8: at least one reason code is mandatory."""
        with pytest.raises(ValidationError):
            self._valid_output(severity_reason_codes=[])

    def test_severity_score_boundary_zero(self):
        out = self._valid_output(severity_score=0)
        assert out.severity_score == 0

    def test_severity_score_boundary_hundred(self):
        out = self._valid_output(severity_score=100)
        assert out.severity_score == 100


# ── Enum sanity checks ────────────────────────────────────────────────────────

class TestEnums:
    def test_incident_types_exact_count(self):
        """FR-6: exactly 8 categories."""
        assert len(IncidentType) == 8

    def test_all_incident_types_present(self):
        types = {t.value for t in IncidentType}
        assert types == {
            "structural_collapse",
            "flooding",
            "medical",
            "trapped_persons",
            "fire",
            "landslide",
            "infrastructure",
            "other",
        }

    def test_severity_labels(self):
        assert SeverityLabel.CRITICAL.value == "CRITICAL"
        assert SeverityLabel.HIGH.value == "HIGH"
        assert SeverityLabel.MEDIUM.value == "MEDIUM"
        assert SeverityLabel.LOW.value == "LOW"

    def test_fallback_states(self):
        states = {s.value for s in FallbackState}
        assert "NORMAL" in states
        assert "RULE_BASED" in states
        assert "AI_BACKFILL_PENDING" in states
        assert "AI_UNAVAILABLE" in states


# ── RiskFactors validation ────────────────────────────────────────────────────

class TestRiskFactors:
    def test_defaults_are_safe(self):
        rf = RiskFactors()
        assert rf.people_at_risk == 0
        assert rf.trapped_persons is False

    def test_negative_people_raises(self):
        with pytest.raises(ValidationError):
            RiskFactors(people_at_risk=-1)

    def test_full_risk_factors(self):
        rf = RiskFactors(
            people_at_risk=5,
            trapped_persons=True,
            medical_emergency=True,
            rapidly_rising_water=True,
        )
        assert rf.people_at_risk == 5
        assert rf.trapped_persons is True


# ── AIErrorResponse structure ─────────────────────────────────────────────────

class TestAIErrorResponse:
    def test_error_structure(self):
        resp = AIErrorResponse(
            error=AIErrorDetail(
                code="AI_PROVIDER_UNAVAILABLE",
                message="Local Granite provider is unavailable.",
                retryable=True,
            )
        )
        assert resp.error.code == "AI_PROVIDER_UNAVAILABLE"
        assert resp.error.retryable is True
