"""
Tests for the rule-based fallback classifier.
All cases must be deterministic with zero external dependencies.
"""

import pytest

from ai_engine.classification.rule_based import classify_rule_based
from shared.schemas.classification import (
    ClassificationReasonCode,
    FallbackState,
    IncidentType,
    ScoringProvider,
)


class TestRuleBasedClassifier:
    """Tests for all 8 fixed incident categories (FR-6)."""

    def test_flooding_house_flooded(self):
        result = classify_rule_based("Water has entered our house and the street is flooded.")
        assert result.incident_type == IncidentType.FLOODING
        assert result.provider == ScoringProvider.RULE_BASED
        assert result.fallback_state == FallbackState.RULE_BASED

    def test_flooding_water_rising(self):
        result = classify_rule_based("The water is rising and we cannot leave.")
        assert result.incident_type == IncidentType.FLOODING

    def test_flooding_submerged(self):
        result = classify_rule_based("The ground floor is completely submerged.")
        assert result.incident_type == IncidentType.FLOODING

    def test_fire_flames(self):
        result = classify_rule_based("Flames and heavy smoke are coming from the building.")
        assert result.incident_type == IncidentType.FIRE

    def test_fire_burning(self):
        result = classify_rule_based("The house is burning rapidly.")
        assert result.incident_type == IncidentType.FIRE

    def test_medical_unconscious(self):
        result = classify_rule_based("My father is unconscious and bleeding.")
        assert result.incident_type == IncidentType.MEDICAL

    def test_medical_heart_attack(self):
        result = classify_rule_based("Someone is having a heart attack here.")
        assert result.incident_type == IncidentType.MEDICAL

    def test_trapped_persons(self):
        result = classify_rule_based("We cannot get out of the second floor.")
        assert result.incident_type == IncidentType.TRAPPED_PERSONS

    def test_trapped_stuck(self):
        result = classify_rule_based("Three people are stuck inside the elevator.")
        assert result.incident_type == IncidentType.TRAPPED_PERSONS

    def test_landslide_hillside(self):
        result = classify_rule_based("The entire hillside has collapsed onto the road.")
        assert result.incident_type == IncidentType.LANDSLIDE

    def test_landslide_mudslide(self):
        result = classify_rule_based("A mudslide has blocked the main road.")
        assert result.incident_type == IncidentType.LANDSLIDE

    def test_infrastructure_bridge(self):
        result = classify_rule_based("The bridge is damaged and vehicles cannot cross.")
        assert result.incident_type == IncidentType.INFRASTRUCTURE

    def test_infrastructure_power(self):
        result = classify_rule_based("There is a power outage and electricity failure.")
        assert result.incident_type == IncidentType.INFRASTRUCTURE

    def test_structural_collapse_building(self):
        result = classify_rule_based("Five people are trapped inside a collapsed building.")
        # Either structural_collapse or trapped_persons is acceptable per precedence policy
        assert result.incident_type in (
            IncidentType.STRUCTURAL_COLLAPSE,
            IncidentType.TRAPPED_PERSONS,
        )

    def test_other_vague(self):
        result = classify_rule_based("Something unusual happened in the area.")
        assert result.incident_type == IncidentType.OTHER
        assert ClassificationReasonCode.GENERIC_INCIDENT in result.reason_codes

    def test_confidence_range(self):
        result = classify_rule_based("The house is flooded and water is rising fast.")
        assert 0.0 <= result.confidence <= 1.0

    def test_confidence_never_above_085(self):
        """Rule-based confidence must remain below 0.85 to be honest about its limits."""
        result = classify_rule_based("Flood water flooded flooding flood submerged rising.")
        assert result.confidence <= 0.85

    def test_reason_codes_present(self):
        result = classify_rule_based("The house is on fire.")
        assert len(result.reason_codes) > 0

    def test_determinism(self):
        """Same input must always produce same output."""
        desc = "Five people are trapped in rising flood water."
        r1 = classify_rule_based(desc)
        r2 = classify_rule_based(desc)
        assert r1.incident_type == r2.incident_type
        assert r1.confidence == r2.confidence
        assert sorted(c.value for c in r1.reason_codes) == sorted(c.value for c in r2.reason_codes)

    def test_empty_string_returns_other(self):
        """Edge case: empty-ish text should gracefully return OTHER."""
        result = classify_rule_based("   ")
        assert result.incident_type == IncidentType.OTHER
