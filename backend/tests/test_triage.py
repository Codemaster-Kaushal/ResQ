"""Phase 4: severity scoring (TRD §4.1).

Per TRD §8 the scoring functions are tested above all else — they are the product, and
they are what judges will interrogate. Each component must contribute independently and
clamping must hold at both bounds.
"""

from __future__ import annotations

import pytest

from app.ai.base import TriageResult
from app.models.enums import IncidentType
from app.services.triage import (
    INCIDENT_WEIGHTS,
    LIFE_RISK_CAP,
    LIFE_RISK_WEIGHTS,
    VULNERABILITY_CAP,
    VULNERABILITY_WEIGHTS,
    canonical_life_risk,
    canonical_vulnerability,
    compute_severity,
    people_band,
)


def result(**overrides) -> TriageResult:
    defaults = dict(
        incident_type=IncidentType.OTHER,
        life_risk_terms=[],
        people_affected_estimate=None,
        vulnerability_terms=[],
        visual_severity_modifier=0,
        confidence=0.5,
    )
    defaults.update(overrides)
    return TriageResult(**defaults)


def codes(severity) -> list[str]:
    return [item["code"] for item in severity.reasons]


# --- The core invariant ---------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"incident_type": IncidentType.TRAPPED_PERSONS},
        {"life_risk_terms": ["trapped", "bleeding"]},
        {"people_affected_estimate": 40},
        {"vulnerability_terms": ["children", "elderly", "disabled", "injured"]},
        {"visual_severity_modifier": 7},
        {"visual_severity_modifier": -9},
        {
            "incident_type": IncidentType.TRAPPED_PERSONS,
            "life_risk_terms": list(LIFE_RISK_WEIGHTS),
            "people_affected_estimate": 500,
            "vulnerability_terms": list(VULNERABILITY_WEIGHTS),
            "visual_severity_modifier": 10,
        },
    ],
)
def test_reason_weights_always_sum_to_the_score(payload) -> None:
    """Without this, 'explainable' is decorative. Capping and clamping are recorded."""
    severity = compute_severity(result(**payload), has_image=True)

    assert sum(item["weight"] for item in severity.reasons) == severity.score


def test_every_score_carries_at_least_one_reason() -> None:
    """FR-8: a score with no explanation is a failed requirement."""
    severity = compute_severity(result(), has_image=False)

    assert severity.reasons
    assert all({"code", "weight", "source"} == set(item) for item in severity.reasons)


# --- Components contribute independently -------------------------------------------------


@pytest.mark.parametrize(("incident", "weight"), sorted(INCIDENT_WEIGHTS.items(), key=str))
def test_incident_weight_is_the_baseline(incident: IncidentType, weight: int) -> None:
    severity = compute_severity(result(incident_type=incident), has_image=False)

    assert severity.score == weight
    assert codes(severity) == [f"INCIDENT_{incident.value.upper()}"]


def test_life_risk_adds_on_top_of_the_incident_weight() -> None:
    base = compute_severity(result(incident_type=IncidentType.MEDICAL), has_image=False)
    with_risk = compute_severity(
        result(incident_type=IncidentType.MEDICAL, life_risk_terms=["bleeding"]),
        has_image=False,
    )

    assert with_risk.score == base.score + LIFE_RISK_WEIGHTS["bleeding"]
    assert "LIFE_RISK_BLEEDING" in codes(with_risk)


def test_life_risk_is_capped_at_thirty() -> None:
    severity = compute_severity(
        result(incident_type=IncidentType.OTHER, life_risk_terms=list(LIFE_RISK_WEIGHTS)),
        has_image=False,
    )

    contribution = severity.score - INCIDENT_WEIGHTS[IncidentType.OTHER]
    assert contribution == LIFE_RISK_CAP


def test_capping_truncates_the_last_reason_rather_than_dropping_it() -> None:
    """The reasons must still add up once the cap bites."""
    severity = compute_severity(
        result(life_risk_terms=list(LIFE_RISK_WEIGHTS)), has_image=False
    )

    life_risk_reasons = [item for item in severity.reasons if item["code"].startswith("LIFE_RISK")]
    assert sum(item["weight"] for item in life_risk_reasons) == LIFE_RISK_CAP


def test_duplicate_terms_are_only_counted_once() -> None:
    once = compute_severity(result(life_risk_terms=["trapped"]), has_image=False)
    twice = compute_severity(
        result(life_risk_terms=["trapped", "trapped", "TRAPPED"]), has_image=False
    )

    assert once.score == twice.score


@pytest.mark.parametrize(
    ("count", "expected_weight", "suffix"),
    [(1, 3, "1"), (2, 7, "2_5"), (5, 7, "2_5"), (6, 12, "6_20"), (20, 12, "6_20"),
     (21, 15, "20_PLUS"), (5000, 15, "20_PLUS")],
)
def test_people_affected_bands(count: int, expected_weight: int, suffix: str) -> None:
    assert people_band(count) == (expected_weight, suffix)

    severity = compute_severity(result(people_affected_estimate=count), has_image=False)
    assert f"PEOPLE_AFFECTED_{suffix}" in codes(severity)


@pytest.mark.parametrize("count", [None, 0])
def test_no_head_count_contributes_nothing(count) -> None:
    severity = compute_severity(result(people_affected_estimate=count), has_image=False)

    assert severity.score == INCIDENT_WEIGHTS[IncidentType.OTHER]
    assert not any(code.startswith("PEOPLE_AFFECTED") for code in codes(severity))


def test_vulnerability_is_capped_at_fifteen() -> None:
    severity = compute_severity(
        result(vulnerability_terms=list(VULNERABILITY_WEIGHTS)), has_image=False
    )

    contribution = severity.score - INCIDENT_WEIGHTS[IncidentType.OTHER]
    assert contribution == VULNERABILITY_CAP


# --- Image modifier -----------------------------------------------------------------------


def test_image_modifier_applies_only_when_an_image_exists() -> None:
    """Absent image → modifier 0, and no failure (TRD §4.1)."""
    payload = result(incident_type=IncidentType.FIRE, visual_severity_modifier=8)

    with_image = compute_severity(payload, has_image=True)
    without_image = compute_severity(payload, has_image=False)

    assert with_image.score == INCIDENT_WEIGHTS[IncidentType.FIRE] + 8
    assert without_image.score == INCIDENT_WEIGHTS[IncidentType.FIRE]
    assert "IMAGE_CORROBORATION" in codes(with_image)
    assert not any(code.startswith("IMAGE_") for code in codes(without_image))


def test_a_contradicting_image_lowers_the_score() -> None:
    severity = compute_severity(
        result(incident_type=IncidentType.FIRE, visual_severity_modifier=-6), has_image=True
    )

    assert severity.score == INCIDENT_WEIGHTS[IncidentType.FIRE] - 6
    assert "IMAGE_CONTRADICTION" in codes(severity)


def test_modifier_is_clamped_to_the_documented_range() -> None:
    """A model returning 40 is wrong, but its other signals are still usable."""
    assert TriageResult(visual_severity_modifier=40).visual_severity_modifier == 10
    assert TriageResult(visual_severity_modifier=-40).visual_severity_modifier == -10
    assert TriageResult(visual_severity_modifier="rubbish").visual_severity_modifier == 0


# --- Clamping ------------------------------------------------------------------------------


def test_score_clamps_at_one_hundred() -> None:
    """Maximum possible raw total is 110, so the upper clamp is reachable."""
    severity = compute_severity(
        result(
            incident_type=IncidentType.TRAPPED_PERSONS,
            life_risk_terms=list(LIFE_RISK_WEIGHTS),
            people_affected_estimate=500,
            vulnerability_terms=list(VULNERABILITY_WEIGHTS),
            visual_severity_modifier=10,
        ),
        has_image=True,
    )

    assert severity.score == 100
    assert "SCORE_CLAMPED_AT_MAX" in codes(severity)


def test_score_clamps_at_zero() -> None:
    severity = compute_severity(
        result(incident_type=IncidentType.OTHER, visual_severity_modifier=-10), has_image=True
    )

    assert severity.score == 0
    assert severity.score >= 0


def test_scores_stay_in_range_across_every_combination() -> None:
    for incident in IncidentType:
        for modifier in (-10, 0, 10):
            for count in (None, 1, 25):
                severity = compute_severity(
                    result(
                        incident_type=incident,
                        life_risk_terms=list(LIFE_RISK_WEIGHTS),
                        vulnerability_terms=list(VULNERABILITY_WEIGHTS),
                        people_affected_estimate=count,
                        visual_severity_modifier=modifier,
                    ),
                    has_image=True,
                )
                assert 0 <= severity.score <= 100


# --- Ordering the demo depends on ------------------------------------------------------------


def test_a_trapped_person_outranks_a_blocked_drain() -> None:
    critical = compute_severity(
        result(
            incident_type=IncidentType.TRAPPED_PERSONS,
            life_risk_terms=["trapped", "no_exit"],
            people_affected_estimate=20,
            vulnerability_terms=["children"],
        ),
        has_image=False,
    )
    minor = compute_severity(result(incident_type=IncidentType.INFRASTRUCTURE), has_image=False)

    assert critical.score > minor.score * 3


# --- Canonicalising model output ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["trapped"], ["trapped"]),
        (["Not Breathing"], ["not_breathing"]),
        (["not-breathing"], ["not_breathing"]),
        (["victim is unresponsive"], ["unconscious"]),
        (["heavy blood loss"], ["bleeding"]),
        (["the water is rising"], ["rising_water"]),
        (["nonsense term"], []),
        ([], []),
    ],
)
def test_life_risk_terms_are_canonicalised(raw, expected) -> None:
    """Remote models return prose; only the canonical vocabulary carries a weight."""
    assert canonical_life_risk(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["children"], ["children"]),
        (["two small kids"], ["children"]),
        (["an elderly resident"], ["elderly"]),
        (["woman in labour"], ["pregnant"]),
        (["unknown"], []),
    ],
)
def test_vulnerability_terms_are_canonicalised(raw, expected) -> None:
    assert canonical_vulnerability(raw) == expected


def test_unknown_terms_never_reach_the_score() -> None:
    """An invented term has no defensible weight, so it must not move the number."""
    baseline = compute_severity(result(incident_type=IncidentType.FIRE), has_image=False)
    with_noise = compute_severity(
        result(
            incident_type=IncidentType.FIRE,
            life_risk_terms=["quantum instability", "vibes"],
            vulnerability_terms=["astrology"],
        ),
        has_image=False,
    )

    assert with_noise.score == baseline.score
