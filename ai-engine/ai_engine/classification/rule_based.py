"""
Rule-based fallback classifier.

This is NOT intended to replace Granite.
It exists for: offline reliability, AI failure, testing, and demo robustness.
Classification provider is always reported as RULE_BASED — never claimed to be AI.

Keyword mapping is fully configurable via KEYWORD_RULES below.
"""

import logging
import re
from dataclasses import dataclass, field

from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class KeywordRule:
    """Associates a keyword pattern with an incident type and reason code."""
    pattern: re.Pattern
    incident_type: IncidentType
    reason_code: ClassificationReasonCode
    weight: float = 1.0  # higher = stronger signal


# ── Configurable keyword rules ────────────────────────────────────────────────
# Each rule carries a weight so that ties can be broken by signal strength.
# Add or modify entries here without touching classifier logic.

KEYWORD_RULES: list[KeywordRule] = [
    # ── Flooding ──────────────────────────────────────────────────────────────
    KeywordRule(re.compile(r"flood", re.I), IncidentType.FLOODING, ClassificationReasonCode.FLOOD_WATER_DETECTED, 2.0),
    KeywordRule(re.compile(r"water\s+(?:is\s+)?rising", re.I), IncidentType.FLOODING, ClassificationReasonCode.WATER_RISING, 2.0),
    KeywordRule(re.compile(r"water\s+entered", re.I), IncidentType.FLOODING, ClassificationReasonCode.FLOOD_WATER_DETECTED, 1.5),
    KeywordRule(re.compile(r"submerged", re.I), IncidentType.FLOODING, ClassificationReasonCode.FLOOD_WATER_DETECTED, 1.5),
    KeywordRule(re.compile(r"inundated?", re.I), IncidentType.FLOODING, ClassificationReasonCode.FLOOD_WATER_DETECTED, 1.5),

    # ── Fire ─────────────────────────────────────────────────────────────────
    KeywordRule(re.compile(r"\bfire\b", re.I), IncidentType.FIRE, ClassificationReasonCode.FIRE_DETECTED, 2.0),
    KeywordRule(re.compile(r"\bburning\b", re.I), IncidentType.FIRE, ClassificationReasonCode.FIRE_DETECTED, 2.0),
    KeywordRule(re.compile(r"flames?", re.I), IncidentType.FIRE, ClassificationReasonCode.FIRE_DETECTED, 1.5),
    KeywordRule(re.compile(r"\bsmoke\b", re.I), IncidentType.FIRE, ClassificationReasonCode.SMOKE_DETECTED, 1.0),
    KeywordRule(re.compile(r"\bblaze\b", re.I), IncidentType.FIRE, ClassificationReasonCode.FIRE_DETECTED, 1.5),

    # ── Medical ───────────────────────────────────────────────────────────────
    KeywordRule(re.compile(r"\binjur", re.I), IncidentType.MEDICAL, ClassificationReasonCode.INJURY_REPORTED, 1.5),
    KeywordRule(re.compile(r"bleeding", re.I), IncidentType.MEDICAL, ClassificationReasonCode.INJURY_REPORTED, 2.0),
    KeywordRule(re.compile(r"unconscious", re.I), IncidentType.MEDICAL, ClassificationReasonCode.UNCONSCIOUS_PERSON, 2.0),
    KeywordRule(re.compile(r"heart\s+attack", re.I), IncidentType.MEDICAL, ClassificationReasonCode.MEDICAL_EMERGENCY, 2.0),
    KeywordRule(re.compile(r"medical\s+emergency", re.I), IncidentType.MEDICAL, ClassificationReasonCode.MEDICAL_EMERGENCY, 2.0),
    KeywordRule(re.compile(r"paramedic", re.I), IncidentType.MEDICAL, ClassificationReasonCode.MEDICAL_EMERGENCY, 1.0),
    KeywordRule(re.compile(r"ambulance", re.I), IncidentType.MEDICAL, ClassificationReasonCode.MEDICAL_EMERGENCY, 1.0),

    # ── Trapped persons ───────────────────────────────────────────────────────
    KeywordRule(re.compile(r"trapped?", re.I), IncidentType.TRAPPED_PERSONS, ClassificationReasonCode.PERSONS_TRAPPED, 2.0),
    KeywordRule(re.compile(r"\bstuck\b", re.I), IncidentType.TRAPPED_PERSONS, ClassificationReasonCode.PERSONS_TRAPPED, 1.5),
    KeywordRule(re.compile(r"cannot\s+escape", re.I), IncidentType.TRAPPED_PERSONS, ClassificationReasonCode.UNABLE_TO_EVACUATE, 2.0),
    KeywordRule(re.compile(r"unable\s+to\s+evacuate", re.I), IncidentType.TRAPPED_PERSONS, ClassificationReasonCode.UNABLE_TO_EVACUATE, 2.0),
    KeywordRule(re.compile(r"can[' ]?t\s+get\s+out", re.I), IncidentType.TRAPPED_PERSONS, ClassificationReasonCode.UNABLE_TO_EVACUATE, 1.5),
    KeywordRule(re.compile(r"cannot\s+get\s+out", re.I), IncidentType.TRAPPED_PERSONS, ClassificationReasonCode.UNABLE_TO_EVACUATE, 1.5),

    # ── Landslide ─────────────────────────────────────────────────────────────
    KeywordRule(re.compile(r"landslide", re.I), IncidentType.LANDSLIDE, ClassificationReasonCode.LANDSLIDE_DETECTED, 2.0),
    KeywordRule(re.compile(r"mudslide", re.I), IncidentType.LANDSLIDE, ClassificationReasonCode.LANDSLIDE_DETECTED, 2.0),
    KeywordRule(re.compile(r"slope\s+(?:\w+\s+)?collaps", re.I), IncidentType.LANDSLIDE, ClassificationReasonCode.SLOPE_COLLAPSE, 2.0),
    KeywordRule(re.compile(r"hillside\s+(?:\w+\s+)?collaps", re.I), IncidentType.LANDSLIDE, ClassificationReasonCode.SLOPE_COLLAPSE, 2.0),
    KeywordRule(re.compile(r"hill\s+(?:\w+\s+)?collaps", re.I), IncidentType.LANDSLIDE, ClassificationReasonCode.SLOPE_COLLAPSE, 2.0),
    KeywordRule(re.compile(r"ground\s+(?:\w+\s+)?collaps", re.I), IncidentType.LANDSLIDE, ClassificationReasonCode.SLOPE_COLLAPSE, 2.0),

    # ── Structural collapse ───────────────────────────────────────────────────
    KeywordRule(re.compile(r"building\s+collaps", re.I), IncidentType.STRUCTURAL_COLLAPSE, ClassificationReasonCode.BUILDING_COLLAPSED, 2.0),
    KeywordRule(re.compile(r"roof\s+collaps", re.I), IncidentType.STRUCTURAL_COLLAPSE, ClassificationReasonCode.STRUCTURAL_DAMAGE, 2.0),
    KeywordRule(re.compile(r"wall\s+collaps", re.I), IncidentType.STRUCTURAL_COLLAPSE, ClassificationReasonCode.STRUCTURAL_DAMAGE, 2.0),
    KeywordRule(re.compile(r"structure\s+collaps", re.I), IncidentType.STRUCTURAL_COLLAPSE, ClassificationReasonCode.BUILDING_COLLAPSED, 2.0),
    KeywordRule(re.compile(r"collaps\w*\s+building", re.I), IncidentType.STRUCTURAL_COLLAPSE, ClassificationReasonCode.BUILDING_COLLAPSED, 2.0),

    # ── Infrastructure ────────────────────────────────────────────────────────
    KeywordRule(re.compile(r"bridge\s+damage", re.I), IncidentType.INFRASTRUCTURE, ClassificationReasonCode.INFRASTRUCTURE_DAMAGE, 2.0),
    KeywordRule(re.compile(r"bridge\s+is\s+damage", re.I), IncidentType.INFRASTRUCTURE, ClassificationReasonCode.INFRASTRUCTURE_DAMAGE, 2.0),
    KeywordRule(re.compile(r"road\s+block", re.I), IncidentType.INFRASTRUCTURE, ClassificationReasonCode.ROAD_BLOCKED, 1.5),
    KeywordRule(re.compile(r"power\s+line", re.I), IncidentType.INFRASTRUCTURE, ClassificationReasonCode.POWER_FAILURE, 1.5),
    KeywordRule(re.compile(r"electricity\s+failure", re.I), IncidentType.INFRASTRUCTURE, ClassificationReasonCode.POWER_FAILURE, 1.5),
    KeywordRule(re.compile(r"power\s+(?:cut|outage|failure)", re.I), IncidentType.INFRASTRUCTURE, ClassificationReasonCode.POWER_FAILURE, 1.5),
    KeywordRule(re.compile(r"vehicles?\s+cannot\s+cross", re.I), IncidentType.INFRASTRUCTURE, ClassificationReasonCode.ROAD_BLOCKED, 2.0),
]


def classify_rule_based(description: str) -> ClassificationResult:
    """
    Deterministic keyword-based incident classification.

    Returns a ClassificationResult with:
    - provider = RULE_BASED
    - fallback_state = RULE_BASED
    - confidence derived from signal weight (never above 0.85 — signals honesty)

    Never raises exceptions; returns IncidentType.OTHER if no match.
    """
    text = description.lower()

    # Accumulate weights per incident type and collect matching reason codes
    type_weights: dict[IncidentType, float] = {}
    type_codes: dict[IncidentType, set[ClassificationReasonCode]] = {}

    for rule in KEYWORD_RULES:
        if rule.pattern.search(text):
            itype = rule.incident_type
            type_weights[itype] = type_weights.get(itype, 0.0) + rule.weight
            type_codes.setdefault(itype, set()).add(rule.reason_code)

    if not type_weights:
        logger.debug("Rule-based classifier: no keywords matched → OTHER")
        return ClassificationResult(
            incident_type=IncidentType.OTHER,
            confidence=0.5,
            reason_codes=[ClassificationReasonCode.GENERIC_INCIDENT],
            provider=ScoringProvider.RULE_BASED,
            fallback_state=FallbackState.RULE_BASED,
        )

    # Pick the type with the highest accumulated weight
    best_type = max(type_weights, key=lambda t: type_weights[t])
    best_weight = type_weights[best_type]

    # Confidence: scale weight into 0.5–0.85, capped below AI confidence
    max_possible = 5.0  # typical cap: 2–3 matching rules at weight 2.0
    confidence = min(0.85, 0.5 + (best_weight / max_possible) * 0.35)

    codes = sorted(type_codes[best_type], key=lambda c: c.value)

    logger.debug(
        "Rule-based classifier: type=%s confidence=%.2f codes=%s",
        best_type,
        confidence,
        codes,
    )

    return ClassificationResult(
        incident_type=best_type,
        confidence=round(confidence, 2),
        reason_codes=codes,
        provider=ScoringProvider.RULE_BASED,
        fallback_state=FallbackState.RULE_BASED,
    )
