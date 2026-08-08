"""Phase 4: the local deterministic scorer.

This is the floor the whole system rests on (NFR-2), so it is tested as a first-class
component rather than as a fallback afterthought.
"""

from __future__ import annotations

import pytest

from app.ai.local import (
    LocalScorer,
    classify_incident,
    detect_life_risk,
    detect_vulnerability,
    estimate_people_affected,
)
from app.models.enums import IncidentType

scorer = LocalScorer()


def classify(text: str) -> IncidentType:
    return classify_incident(text.lower())[0]


# --- Classification ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("at least six people are trapped under the debris", IncidentType.TRAPPED_PERSONS),
        ("the three storey building has collapsed", IncidentType.STRUCTURAL_COLLAPSE),
        ("thick black smoke and flames from the godown", IncidentType.FIRE),
        ("the underpass is flooded to chest height", IncidentType.FLOODING),
        ("a landslide has covered the service road", IncidentType.LANDSLIDE),
        ("he is unconscious and not breathing", IncidentType.MEDICAL),
        ("the street light has been out for two days", IncidentType.INFRASTRUCTURE),
        ("something is happening here", IncidentType.OTHER),
    ],
)
def test_incident_classification(text: str, expected: IncidentType) -> None:
    assert classify(text) == expected


def test_a_person_collapsing_is_medical_not_structural() -> None:
    """'Collapsed' is the same word for a building and for a patient. Getting this
    wrong sends a rescue crew to a cardiac arrest."""
    assert classify("an elderly man has collapsed at the bus stop") == IncidentType.MEDICAL
    assert classify("the compound wall has collapsed onto a car") == IncidentType.STRUCTURAL_COLLAPSE


def test_an_explicit_trapping_outranks_the_hazard_that_caused_it() -> None:
    """Someone pinned in a flooded basement is a rescue call, not a flooding report."""
    text = "the basement is flooded to chest height and the watchman is trapped in the pump room"

    assert classify(text) == IncidentType.TRAPPED_PERSONS


def test_a_crack_in_a_structure_beats_a_bare_crack() -> None:
    assert classify("the boundary wall of the school has cracked badly") == (
        IncidentType.STRUCTURAL_COLLAPSE
    )


def test_a_child_in_a_drain_is_a_rescue_not_a_drain_complaint() -> None:
    text = "a child has fallen into an open drain and people are trying to pull him out"

    assert classify(text) == IncidentType.TRAPPED_PERSONS


def test_water_rising_is_flooding_however_it_is_phrased() -> None:
    assert classify("water is rising fast in the underpass") == IncidentType.FLOODING
    assert classify("there is rising water in the basement") == IncidentType.FLOODING


def test_ties_break_towards_the_more_dangerous_reading() -> None:
    """When evidence is balanced, resolve towards the interpretation risking more lives."""
    incident, _ = classify_incident("building collapse, people trapped")

    assert incident == IncidentType.TRAPPED_PERSONS


# --- Signal extraction ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("he is not breathing", "not_breathing"),
        ("she is unconscious", "unconscious"),
        ("bleeding heavily from the head", "bleeding"),
        ("six people are trapped", "trapped"),
        ("a man is drowning", "drowning"),
        ("the water is rising fast", "rising_water"),
        ("there is no way out of the building", "no_exit"),
    ],
)
def test_life_risk_vocabulary(text: str, expected: str) -> None:
    assert expected in detect_life_risk(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("two small children are inside", "children"),
        ("an elderly couple lived there", "elderly"),
        ("a disabled resident on the ground floor", "disabled"),
        ("a pregnant woman needs an ambulance", "pregnant"),
        ("one worker is injured", "injured"),
    ],
)
def test_vulnerability_vocabulary(text: str, expected: str) -> None:
    assert expected in detect_vulnerability(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("at least six people are trapped", 6),
        ("twenty people are inside", 20),
        ("2 workers injured", 2),
        ("a family with two small children", 2),
        ("an elderly man has collapsed", 1),
        ("the driver cannot move", 1),
        ("smoke is coming from the roof", None),
        ("several residents are affected", 4),
    ],
)
def test_people_estimates(text: str, expected) -> None:
    assert estimate_people_affected(text) == expected


def test_the_largest_credible_count_wins() -> None:
    text = "two cars are stuck and at least fifteen people are waiting"

    assert estimate_people_affected(text) == 15


# --- Provider behaviour -------------------------------------------------------------------


def test_local_scorer_is_always_available() -> None:
    assert scorer.is_available() is True


def test_local_scorer_is_deterministic() -> None:
    """Same text in, same answer out — every time, on every machine."""
    text = "building collapsed, six people trapped under the debris, children crying"

    first = scorer.classify_sync(text)
    second = scorer.classify_sync(text)

    assert first.model_dump() == second.model_dump()


def test_local_scorer_never_claims_a_visual_judgement() -> None:
    """Rules cannot see the photograph, so the modifier stays neutral."""
    assert scorer.classify_sync("fire with thick smoke").visual_severity_modifier == 0


def test_local_confidence_stays_below_certainty() -> None:
    """A heuristic must never present itself as certain."""
    rich = scorer.classify_sync(
        "building collapsed, twenty people trapped under the debris, children and "
        "elderly are bleeding and not breathing"
    )
    sparse = scorer.classify_sync("help")

    assert 0 < sparse.confidence < rich.confidence <= 0.85


@pytest.mark.parametrize("text", ["", "   ", "!!!", "asdfghjkl"])
def test_unhelpful_text_still_produces_a_valid_result(text: str) -> None:
    """Ingestion never rejects a report, so the scorer must always answer."""
    result = scorer.classify_sync(text)

    assert result.incident_type == IncidentType.OTHER
    assert result.life_risk_terms == []


@pytest.mark.asyncio
async def test_async_interface_matches_the_sync_one() -> None:
    text = "flooding in the basement, a watchman is trapped"

    assert (await scorer.classify(text, None)).model_dump() == scorer.classify_sync(text).model_dump()
