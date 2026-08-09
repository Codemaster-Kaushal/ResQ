"""
Tests for the severity engine — FR-7 (0–100 score) and FR-8 (reason codes).
"""

import pytest

from ai_engine.severity.engine import calculate_severity, _label_from_score
from ai_engine.severity.config import CRITICAL_THRESHOLD, HIGH_THRESHOLD, MEDIUM_THRESHOLD
from shared.schemas.severity import (
    RiskFactors,
    SeverityLabel,
    SeverityReasonCode,
    SeverityResult,
)


class TestSeverityLabels:
    def test_critical_threshold(self):
        assert _label_from_score(CRITICAL_THRESHOLD) == SeverityLabel.CRITICAL
        assert _label_from_score(100) == SeverityLabel.CRITICAL
        assert _label_from_score(80) == SeverityLabel.CRITICAL

    def test_high_threshold(self):
        assert _label_from_score(HIGH_THRESHOLD) == SeverityLabel.HIGH
        assert _label_from_score(79) == SeverityLabel.HIGH

    def test_medium_threshold(self):
        assert _label_from_score(MEDIUM_THRESHOLD) == SeverityLabel.MEDIUM
        assert _label_from_score(59) == SeverityLabel.MEDIUM

    def test_low(self):
        assert _label_from_score(0) == SeverityLabel.LOW
        assert _label_from_score(39) == SeverityLabel.LOW


class TestSeverityCalculation:
    def test_critical_multiple_risks(self):
        """FR-7: five people trapped with medical emergency and rising water = CRITICAL."""
        risk = RiskFactors(
            people_at_risk=5,
            trapped_persons=True,
            medical_emergency=True,
            rapidly_rising_water=True,
        )
        result = calculate_severity(risk, classification_confidence=0.9)
        assert result.severity_score >= CRITICAL_THRESHOLD
        assert result.severity_label == SeverityLabel.CRITICAL

    def test_low_minimal_risk(self):
        """Small water near road with no people = LOW."""
        risk = RiskFactors(people_at_risk=0, environmental_danger=True)
        result = calculate_severity(risk, classification_confidence=0.5)
        assert result.severity_label == SeverityLabel.LOW

    def test_high_two_people_need_help(self):
        """Flood water in house, 2 people need help (trapped) → HIGH or CRITICAL."""
        risk = RiskFactors(
            people_at_risk=2,
            trapped_persons=True,
            rapidly_rising_water=True,
        )
        result = calculate_severity(risk, classification_confidence=0.8)
        assert result.severity_label in (SeverityLabel.HIGH, SeverityLabel.CRITICAL)

    def test_score_clamped_to_100(self):
        """Score must never exceed 100."""
        risk = RiskFactors(
            people_at_risk=100,
            trapped_persons=True,
            medical_emergency=True,
            rapidly_rising_water=True,
            structural_damage=True,
            fire_present=True,
            infrastructure_failure=True,
            evacuation_impossible=True,
            vulnerable_people=True,
            environmental_danger=True,
        )
        result = calculate_severity(risk, classification_confidence=1.0)
        assert result.severity_score <= 100

    def test_score_clamped_to_zero(self):
        """Score must never be negative."""
        risk = RiskFactors()
        result = calculate_severity(risk, classification_confidence=0.0)
        assert result.severity_score >= 0

    def test_reason_codes_never_empty(self):
        """FR-8: at least one reason code is always returned."""
        risk = RiskFactors()
        result = calculate_severity(risk)
        assert len(result.severity_reason_codes) >= 1

    def test_insufficient_risk_code_when_no_factors(self):
        """FR-8: when no risk factors detected, INSUFFICIENT_RISK_INFORMATION is returned."""
        risk = RiskFactors()
        result = calculate_severity(risk)
        assert SeverityReasonCode.INSUFFICIENT_RISK_INFORMATION in result.severity_reason_codes

    def test_multiple_people_reason_code(self):
        risk = RiskFactors(people_at_risk=5)
        result = calculate_severity(risk)
        assert SeverityReasonCode.MULTIPLE_PEOPLE_AT_RISK in result.severity_reason_codes

    def test_single_person_reason_code(self):
        risk = RiskFactors(people_at_risk=1)
        result = calculate_severity(risk)
        assert SeverityReasonCode.PEOPLE_AT_RISK in result.severity_reason_codes

    def test_trapped_persons_reason_code(self):
        risk = RiskFactors(trapped_persons=True)
        result = calculate_severity(risk)
        assert SeverityReasonCode.TRAPPED_PERSONS in result.severity_reason_codes

    def test_medical_emergency_reason_code(self):
        risk = RiskFactors(medical_emergency=True)
        result = calculate_severity(risk)
        assert SeverityReasonCode.MEDICAL_EMERGENCY in result.severity_reason_codes

    def test_fire_reason_code(self):
        risk = RiskFactors(fire_present=True)
        result = calculate_severity(risk)
        assert SeverityReasonCode.FIRE_PRESENT in result.severity_reason_codes

    def test_structural_reason_code(self):
        risk = RiskFactors(structural_damage=True)
        result = calculate_severity(risk)
        assert SeverityReasonCode.STRUCTURAL_DAMAGE in result.severity_reason_codes

    def test_returns_severity_result_model(self):
        risk = RiskFactors(people_at_risk=3, trapped_persons=True)
        result = calculate_severity(risk)
        assert isinstance(result, SeverityResult)

    def test_determinism(self):
        """Same input must always produce same score (FR-10 / determinism)."""
        risk = RiskFactors(
            people_at_risk=5,
            trapped_persons=True,
            medical_emergency=True,
            rapidly_rising_water=True,
        )
        r1 = calculate_severity(risk, classification_confidence=0.9)
        r2 = calculate_severity(risk, classification_confidence=0.9)
        assert r1.severity_score == r2.severity_score
        assert r1.severity_label == r2.severity_label
        assert sorted(c.value for c in r1.severity_reason_codes) == sorted(
            c.value for c in r2.severity_reason_codes
        )

    def test_zero_people_does_not_add_people_reason_code(self):
        risk = RiskFactors(people_at_risk=0, trapped_persons=False)
        result = calculate_severity(risk)
        assert SeverityReasonCode.PEOPLE_AT_RISK not in result.severity_reason_codes
        assert SeverityReasonCode.MULTIPLE_PEOPLE_AT_RISK not in result.severity_reason_codes

    def test_image_null_continues_successfully(self):
        """FR-9: image=None must not affect severity calculation."""
        risk = RiskFactors(people_at_risk=4, trapped_persons=True)
        result = calculate_severity(risk)
        assert result.severity_score >= 0
