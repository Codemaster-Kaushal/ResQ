"""
Tests for rule-based risk factor extraction.
"""

import pytest

from ai_engine.classification.risk_extractor import extract_risk_factors_rule_based
from shared.schemas.severity import RiskFactors


class TestRuleBasedRiskExtractor:
    def test_people_count_extracted(self):
        rf = extract_risk_factors_rule_based("Five people are trapped on the roof.")
        assert rf.people_at_risk == 5

    def test_multiple_people_takes_max(self):
        rf = extract_risk_factors_rule_based("2 people are injured and 4 people are trapped.")
        assert rf.people_at_risk == 4

    def test_trapped_detected(self):
        rf = extract_risk_factors_rule_based("People are trapped inside.")
        assert rf.trapped_persons is True

    def test_medical_detected_bleeding(self):
        rf = extract_risk_factors_rule_based("My father is bleeding and unconscious.")
        assert rf.medical_emergency is True

    def test_medical_detected_unconscious(self):
        rf = extract_risk_factors_rule_based("Someone is unconscious on the floor.")
        assert rf.medical_emergency is True

    def test_rapidly_rising_water(self):
        rf = extract_risk_factors_rule_based("The water is rising rapidly.")
        assert rf.rapidly_rising_water is True

    def test_fire_detected(self):
        rf = extract_risk_factors_rule_based("Flames are spreading across the building.")
        assert rf.fire_present is True

    def test_structural_damage(self):
        rf = extract_risk_factors_rule_based("The wall collapsed and there is structural damage.")
        assert rf.structural_damage is True

    def test_infrastructure_failure(self):
        rf = extract_risk_factors_rule_based("Power outage and road blocked.")
        assert rf.infrastructure_failure is True

    def test_evacuation_impossible(self):
        rf = extract_risk_factors_rule_based("Evacuation is impossible because roads are blocked.")
        assert rf.evacuation_impossible is True

    def test_vulnerable_people_children(self):
        rf = extract_risk_factors_rule_based("There are children and elderly residents inside.")
        assert rf.vulnerable_people is True

    def test_environmental_danger_flood(self):
        rf = extract_risk_factors_rule_based("The area is flooded and hazardous.")
        assert rf.environmental_danger is True

    def test_no_risk_factors_all_false(self):
        rf = extract_risk_factors_rule_based("Everything is calm and normal.")
        assert rf.people_at_risk == 0
        assert rf.trapped_persons is False
        assert rf.medical_emergency is False
        assert rf.fire_present is False

    def test_zero_people_when_none_mentioned(self):
        rf = extract_risk_factors_rule_based("There is smoke coming from the building.")
        assert rf.people_at_risk == 0

    def test_returns_valid_risk_factors_model(self):
        rf = extract_risk_factors_rule_based("4 people trapped, one injured, water rising.")
        assert isinstance(rf, RiskFactors)
        assert rf.people_at_risk == 4
        assert rf.trapped_persons is True
        assert rf.medical_emergency is True

    def test_determinism(self):
        desc = "Five people are trapped on the second floor. My father is injured."
        rf1 = extract_risk_factors_rule_based(desc)
        rf2 = extract_risk_factors_rule_based(desc)
        assert rf1.model_dump() == rf2.model_dump()

    def test_image_null_does_not_break(self):
        """FR-9: risk extraction must work with image=None (text-only mode)."""
        rf = extract_risk_factors_rule_based("Four people are trapped in a flooded house")
        assert isinstance(rf, RiskFactors)
