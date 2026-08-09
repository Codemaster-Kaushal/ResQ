"""
Deterministic severity engine — FR-7 and FR-8.

Architecture:
    Granite extracts structured RiskFactors  (or rule-based fallback)
        ↓
    calculate_severity() applies configured weights  ← deterministic maths
        ↓
    SeverityResult with clamped score, label, and reason codes

Score is ALWAYS deterministic for a given set of RiskFactors.
The AI layer (Granite) only affects the *extracted factors*, not the scoring formula.
"""

import logging
from typing import Optional

from ai_engine.severity.config import (
    CRITICAL_THRESHOLD,
    HIGH_THRESHOLD,
    MEDIUM_THRESHOLD,
    PEOPLE_AT_RISK_SCALE,
    RISK_FACTOR_WEIGHTS,
)
from shared.schemas.severity import (
    RiskFactors,
    SeverityLabel,
    SeverityReasonCode,
    SeverityResult,
)

logger = logging.getLogger(__name__)


def _label_from_score(score: int) -> SeverityLabel:
    """Convert a numeric score to a SeverityLabel."""
    if score >= CRITICAL_THRESHOLD:
        return SeverityLabel.CRITICAL
    if score >= HIGH_THRESHOLD:
        return SeverityLabel.HIGH
    if score >= MEDIUM_THRESHOLD:
        return SeverityLabel.MEDIUM
    return SeverityLabel.LOW


def _people_fraction(count: int) -> float:
    """Interpolate a [0,1] fraction from people_at_risk count using configured scale."""
    scale = PEOPLE_AT_RISK_SCALE
    if count <= 0:
        return scale[0][1]
    for i in range(len(scale) - 1):
        lo_count, lo_frac = scale[i]
        hi_count, hi_frac = scale[i + 1]
        if lo_count <= count <= hi_count:
            span = hi_count - lo_count
            if span == 0:
                return hi_frac
            t = (count - lo_count) / span
            return lo_frac + t * (hi_frac - lo_frac)
    return scale[-1][1]


def _build_reason_codes(
    risk: RiskFactors, classification_confidence: float = 0.5
) -> list[SeverityReasonCode]:
    """
    Build reason codes from active risk factors (FR-8).
    At least one code is always returned.
    """
    codes: list[SeverityReasonCode] = []

    if risk.people_at_risk >= 3:
        codes.append(SeverityReasonCode.MULTIPLE_PEOPLE_AT_RISK)
    elif risk.people_at_risk >= 1:
        codes.append(SeverityReasonCode.PEOPLE_AT_RISK)

    if risk.trapped_persons:
        codes.append(SeverityReasonCode.TRAPPED_PERSONS)

    if risk.medical_emergency:
        codes.append(SeverityReasonCode.MEDICAL_EMERGENCY)

    if risk.rapidly_rising_water:
        codes.append(SeverityReasonCode.RAPIDLY_RISING_WATER)

    if risk.structural_damage:
        codes.append(SeverityReasonCode.STRUCTURAL_DAMAGE)

    if risk.fire_present:
        codes.append(SeverityReasonCode.FIRE_PRESENT)

    if risk.infrastructure_failure:
        codes.append(SeverityReasonCode.INFRASTRUCTURE_FAILURE)

    if risk.evacuation_impossible:
        codes.append(SeverityReasonCode.EVACUATION_IMPOSSIBLE)

    if risk.vulnerable_people:
        codes.append(SeverityReasonCode.VULNERABLE_PEOPLE)

    if risk.environmental_danger:
        codes.append(SeverityReasonCode.ENVIRONMENTAL_DANGER)

    if not codes:
        codes.append(SeverityReasonCode.INSUFFICIENT_RISK_INFORMATION)

    return codes


def calculate_severity(
    risk: RiskFactors,
    classification_confidence: float = 0.5,
    incident_type_penalty: float = 0.0,
) -> SeverityResult:
    """
    Deterministic severity calculation from structured RiskFactors.

    Hybrid approach:
    - Granite provides structured factors → this function does the maths.
    - Scoring is fully reproducible: same input → same output (FR-10 / determinism requirement).

    Args:
        risk: Extracted risk factors.
        classification_confidence: From the classifier (0–1); feeds "ai_incident_assessment".
        incident_type_penalty: Additional modifier for incident type context (reserved, default 0).

    Returns:
        SeverityResult with clamped score, label, and at least one reason code.
    """
    W = RISK_FACTOR_WEIGHTS

    # ── Component calculations ────────────────────────────────────────────────

    # 1. AI confidence contributes to "AI assessment" component
    ai_component = classification_confidence * W["ai_incident_assessment"]

    # 2. People at risk (scaled non-linearly)
    people_frac = _people_fraction(risk.people_at_risk)
    people_component = people_frac * W["people_at_risk"]

    # 3. Boolean risk factors
    medical_component = W["medical_emergency"] if risk.medical_emergency else 0.0
    trapped_component = W["trapped_persons"] if risk.trapped_persons else 0.0

    # 4. Immediate physical danger: fire OR rapidly_rising_water
    immediate_danger = risk.fire_present or risk.rapidly_rising_water
    immediate_component = W["immediate_physical_danger"] if immediate_danger else 0.0

    # 5. Structural
    structural_component = W["structural_damage"] if risk.structural_damage else 0.0

    # 6. Environmental risk: flooding-related environmental danger
    env_component = W["environmental_risk"] if risk.environmental_danger else 0.0

    # 7. Other context: infra failure or evacuation impossible
    other_context = risk.infrastructure_failure or risk.evacuation_impossible or risk.vulnerable_people
    other_component = W["other_context"] if other_context else 0.0

    # 8. Critical emergency override: severe trapped/high-risk flood/medical situations
    # must reach CRITICAL when the combination of people-at-risk + physical danger is already severe.
    critical_override = (
        risk.people_at_risk >= 5
        and (
            risk.trapped_persons
            or risk.medical_emergency
            or risk.rapidly_rising_water
        )
    )
    critical_boost = 10.0 if critical_override else 0.0

    # ── Sum and clamp ─────────────────────────────────────────────────────────
    raw_score = (
        ai_component
        + people_component
        + medical_component
        + trapped_component
        + immediate_component
        + structural_component
        + env_component
        + other_component
        + incident_type_penalty
        + critical_boost
    )

    score = int(round(max(0.0, min(100.0, raw_score))))
    label = _label_from_score(score)
    reason_codes = _build_reason_codes(risk, classification_confidence)

    logger.debug(
        "Severity calculation: score=%d label=%s components=[ai=%.1f people=%.1f "
        "medical=%.1f trapped=%.1f immediate=%.1f structural=%.1f env=%.1f other=%.1f]",
        score, label,
        ai_component, people_component, medical_component, trapped_component,
        immediate_component, structural_component, env_component, other_component,
    )

    return SeverityResult(
        severity_score=score,
        severity_label=label,
        severity_reason_codes=reason_codes,
        risk_factors=risk,
    )
