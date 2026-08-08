"""Phase 5: authenticity and trust scoring (TRD §4.2).

Runs against its own engine so the seeded dataset cannot influence the arithmetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401  — registers the tables
from app.config import settings
from app.models import IncidentType, Report, ReportStatus
from app.services.authenticity import (
    CORROBORATED_BONUS,
    DUPLICATE_IMAGE_PENALTY,
    GEO_IMPLAUSIBLE_PENALTY,
    IMPOSSIBLE_MOVEMENT_PENALTY,
    LOW_INFORMATION_PENALTY,
    STALE_REPORT_PENALTY,
    apply_authenticity,
    compute_authenticity,
    find_corroborating,
    find_duplicate_image,
    is_low_information,
)

BASE = datetime(2026, 8, 8, 12, 0, 0)
KOR = (12.9352, 77.6245)

# Two hashes 2 bits apart, and one far away.
HASH_A = "c0f038cee30f1f33"
HASH_NEAR = "c0f038cee30f1f31"
HASH_FAR = "0f0fc7311cf0e0cc"


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/auth.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with Session(engine) as db_session:
        yield db_session


def make(
    session: Session,
    *,
    text: str = "the building has collapsed and people are trapped inside",
    lat: float = KOR[0],
    lng: float = KOR[1],
    client_minutes_ago: float = 0,
    received_minutes_ago: float | None = None,
    pseudonym: str = "swift-heron-77",
    phash: str | None = None,
    incident: IncidentType = IncidentType.TRAPPED_PERSONS,
    status: ReportStatus = ReportStatus.CLASSIFIED,
    commit: bool = True,
) -> Report:
    received = client_minutes_ago if received_minutes_ago is None else received_minutes_ago
    report = Report(
        idempotency_key=f"t-{uuid.uuid4()}",
        text=text,
        lat=lat,
        lng=lng,
        client_created_at=BASE - timedelta(minutes=client_minutes_ago),
        received_at=BASE - timedelta(minutes=received),
        reporter_pseudonym=pseudonym,
        image_phash=phash,
        image_path="seed/x.jpg" if phash else None,
        incident_type=incident,
        severity_score=50,
        status=status,
    )
    session.add(report)
    if commit:
        session.commit()
        session.refresh(report)
    return report


def codes(assessment) -> list[str]:
    return [item["code"] for item in assessment.reasons]


# --- Baseline and invariants -----------------------------------------------------------


def test_a_clean_report_sits_at_the_baseline(session: Session) -> None:
    report = make(session)

    assessment = compute_authenticity(session, report)

    assert assessment.score == settings.authenticity_baseline
    assert codes(assessment) == ["BASELINE"]


def test_reason_weights_always_sum_to_the_score(session: Session) -> None:
    """Same invariant as severity: the reasons must add up to the number."""
    first = make(session, phash=HASH_A, client_minutes_ago=600, received_minutes_ago=0)
    second = make(
        session,
        phash=HASH_NEAR,
        lat=0.0,
        lng=0.0,
        text="help",
        pseudonym="other-person",
        client_minutes_ago=5,
    )

    for report in (first, second):
        assessment = compute_authenticity(session, report)
        assert sum(item["weight"] for item in assessment.reasons) == assessment.score


def test_score_is_clamped_to_zero(session: Session) -> None:
    """Stacked penalties reach −65 before clamping."""
    make(session, phash=HASH_A, client_minutes_ago=10, pseudonym="repeat-reporter")
    suspect = make(
        session,
        phash=HASH_NEAR,
        lat=0.0,
        lng=0.0,
        text="help",
        pseudonym="repeat-reporter",
        client_minutes_ago=5,
        received_minutes_ago=-600,
    )

    assessment = compute_authenticity(session, suspect)

    assert assessment.score == 0
    assert "SCORE_CLAMPED_AT_MIN" in codes(assessment)


# --- Duplicate images ---------------------------------------------------------------------


def test_the_later_report_is_penalised_not_the_original(session: Session) -> None:
    """The report that re-uses an image is the suspicious one."""
    original = make(session, phash=HASH_A, received_minutes_ago=60, pseudonym="first-reporter")
    reshare = make(session, phash=HASH_NEAR, received_minutes_ago=30, pseudonym="second-reporter")

    assert "DUPLICATE_IMAGE" not in codes(compute_authenticity(session, original))

    duplicate = compute_authenticity(session, reshare)
    assert "DUPLICATE_IMAGE" in codes(duplicate)
    assert duplicate.score == settings.authenticity_baseline + DUPLICATE_IMAGE_PENALTY


def test_distant_hashes_are_not_duplicates(session: Session) -> None:
    make(session, phash=HASH_A, received_minutes_ago=60)
    other = make(session, phash=HASH_FAR, received_minutes_ago=30, pseudonym="someone-else")

    assert find_duplicate_image(session, other) is None


def test_duplicate_detection_respects_the_configured_distance(
    session: Session, monkeypatch
) -> None:
    make(session, phash=HASH_A, received_minutes_ago=60)
    near = make(session, phash=HASH_NEAR, received_minutes_ago=30, pseudonym="someone-else")

    monkeypatch.setattr(settings, "phash_duplicate_distance", 0)
    assert find_duplicate_image(session, near) is None

    monkeypatch.setattr(settings, "phash_duplicate_distance", 8)
    assert find_duplicate_image(session, near) is not None


def test_a_report_without_an_image_is_never_a_duplicate(session: Session) -> None:
    make(session, phash=HASH_A, received_minutes_ago=60)
    textual = make(session, received_minutes_ago=30, pseudonym="someone-else")

    assert find_duplicate_image(session, textual) is None


def test_reports_sharing_a_receipt_time_do_not_both_get_penalised(session: Session) -> None:
    """Otherwise a tie would punish both halves of a pair."""
    first = make(session, phash=HASH_A, received_minutes_ago=30, pseudonym="a")
    second = make(session, phash=HASH_NEAR, received_minutes_ago=30, pseudonym="b")

    penalised = [
        report
        for report in (first, second)
        if "DUPLICATE_IMAGE" in codes(compute_authenticity(session, report))
    ]

    assert len(penalised) == 1


# --- Corroboration ----------------------------------------------------------------------------


def test_independent_nearby_reports_raise_trust(session: Session) -> None:
    for index in range(2):
        make(session, pseudonym=f"witness-{index}", client_minutes_ago=5, incident=IncidentType.FIRE)
    subject = make(session, pseudonym="witness-9", client_minutes_ago=3, incident=IncidentType.FIRE)

    assessment = compute_authenticity(session, subject)

    assert "CORROBORATED" in codes(assessment)
    assert assessment.score == settings.authenticity_baseline + CORROBORATED_BONUS


def test_one_person_cannot_corroborate_themselves(session: Session) -> None:
    make(session, pseudonym="same-person", client_minutes_ago=5, incident=IncidentType.FIRE)
    subject = make(session, pseudonym="same-person", client_minutes_ago=3, incident=IncidentType.FIRE)

    assert find_corroborating(session, subject) == []


def test_a_recycled_image_is_not_independent_corroboration(session: Session) -> None:
    """Two reports built on one photograph are one observation, not two. Counting them
    as corroboration is the false-amplification this stage exists to stop."""
    make(
        session,
        pseudonym="poster-a",
        phash=HASH_A,
        client_minutes_ago=5,
        incident=IncidentType.FIRE,
    )
    reshare = make(
        session,
        pseudonym="poster-b",
        phash=HASH_NEAR,
        client_minutes_ago=3,
        incident=IncidentType.FIRE,
    )

    assert find_corroborating(session, reshare) == []
    assert "CORROBORATED" not in codes(compute_authenticity(session, reshare))


def test_a_different_incident_nearby_does_not_corroborate(session: Session) -> None:
    """FR-14 says the same *event*. A collision and a flood on one corner are two
    incidents, and neither makes the other more credible."""
    make(session, pseudonym="witness-a", client_minutes_ago=5, incident=IncidentType.FLOODING)
    subject = make(session, pseudonym="witness-b", client_minutes_ago=3, incident=IncidentType.MEDICAL)

    assert find_corroborating(session, subject) == []


def test_same_type_matching_can_be_disabled(session: Session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "corroboration_require_same_type", False)
    make(session, pseudonym="witness-a", client_minutes_ago=5, incident=IncidentType.FLOODING)
    subject = make(session, pseudonym="witness-b", client_minutes_ago=3, incident=IncidentType.MEDICAL)

    assert len(find_corroborating(session, subject)) == 1


def test_reports_outside_the_radius_do_not_corroborate(session: Session) -> None:
    make(session, pseudonym="far-witness", lat=13.5, lng=77.6, client_minutes_ago=5)
    subject = make(session, pseudonym="near-witness", client_minutes_ago=3)

    assert find_corroborating(session, subject) == []


def test_reports_outside_the_time_window_do_not_corroborate(session: Session) -> None:
    make(session, pseudonym="early-witness", client_minutes_ago=600)
    subject = make(session, pseudonym="late-witness", client_minutes_ago=3)

    assert find_corroborating(session, subject) == []


def test_corroboration_uses_the_client_clock_not_receipt(session: Session) -> None:
    """An offline report filed at the same moment still corroborates once it syncs."""
    make(session, pseudonym="witness-a", client_minutes_ago=5, received_minutes_ago=5)
    late_sync = make(
        session, pseudonym="witness-b", client_minutes_ago=6, received_minutes_ago=-600
    )

    assert len(find_corroborating(session, late_sync)) == 1


# --- Geo and time signals ---------------------------------------------------------------------


def test_null_island_is_implausible(session: Session) -> None:
    report = make(session, lat=0.0, lng=0.0)

    assessment = compute_authenticity(session, report)

    assert "GEO_IMPLAUSIBLE" in codes(assessment)
    assert assessment.score == settings.authenticity_baseline + GEO_IMPLAUSIBLE_PENALTY


def test_a_stale_client_clock_costs_trust(session: Session) -> None:
    report = make(session, client_minutes_ago=8 * 60, received_minutes_ago=0)

    assessment = compute_authenticity(session, report)

    assert "STALE_REPORT" in codes(assessment)
    assert assessment.score == settings.authenticity_baseline + STALE_REPORT_PENALTY


def test_a_report_synced_within_the_window_is_not_stale(session: Session) -> None:
    report = make(session, client_minutes_ago=5 * 60, received_minutes_ago=0)

    assert "STALE_REPORT" not in codes(compute_authenticity(session, report))


def test_impossible_movement_between_two_reports(session: Session) -> None:
    make(session, pseudonym="roamer", client_minutes_ago=6)
    mumbai = make(session, pseudonym="roamer", lat=19.0760, lng=72.8777, client_minutes_ago=0)

    assessment = compute_authenticity(session, mumbai)

    assert "IMPOSSIBLE_MOVEMENT" in codes(assessment)
    assert assessment.score == settings.authenticity_baseline + IMPOSSIBLE_MOVEMENT_PENALTY


def test_the_same_journey_over_a_longer_gap_is_plausible(session: Session) -> None:
    make(session, pseudonym="traveller", client_minutes_ago=600)
    mumbai = make(session, pseudonym="traveller", lat=19.0760, lng=72.8777, client_minutes_ago=0)

    assert "IMPOSSIBLE_MOVEMENT" not in codes(compute_authenticity(session, mumbai))


def test_two_different_people_far_apart_is_not_suspicious(session: Session) -> None:
    make(session, pseudonym="person-a", client_minutes_ago=6)
    mumbai = make(session, pseudonym="person-b", lat=19.0760, lng=72.8777, client_minutes_ago=0)

    assert "IMPOSSIBLE_MOVEMENT" not in codes(compute_authenticity(session, mumbai))


# --- Low information ------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["help", "please", "hi there", ""])
def test_short_text_naming_no_incident_is_low_information(text: str) -> None:
    assert is_low_information(text) is True


@pytest.mark.parametrize("text", ["fire", "building collapsed", "flooding here now"])
def test_short_text_naming_an_incident_is_not_low_information(text: str) -> None:
    """Brevity is not the problem — a brief report of a fire is still actionable."""
    assert is_low_information(text) is False


def test_long_text_is_never_low_information() -> None:
    assert is_low_information("one two three four five six seven") is False


def test_low_information_costs_trust(session: Session) -> None:
    report = make(session, text="help")

    assessment = compute_authenticity(session, report)

    assert "LOW_INFORMATION" in codes(assessment)
    assert assessment.score == settings.authenticity_baseline + LOW_INFORMATION_PENALTY


# --- Flagging, and the promise not to reject ----------------------------------------------------------


def test_a_trusted_report_is_verified(session: Session) -> None:
    report = make(session)

    apply_authenticity(report, compute_authenticity(session, report))

    assert report.status == ReportStatus.VERIFIED


def test_a_report_below_the_threshold_is_flagged(session: Session) -> None:
    make(session, phash=HASH_A, received_minutes_ago=60)
    suspect = make(session, phash=HASH_NEAR, received_minutes_ago=30, pseudonym="reposter")

    assessment = compute_authenticity(session, suspect)
    apply_authenticity(suspect, assessment)

    assert assessment.score < settings.authenticity_flag_threshold
    assert suspect.status == ReportStatus.FLAGGED


def test_the_flag_threshold_is_configurable(session: Session, monkeypatch) -> None:
    report = make(session)
    monkeypatch.setattr(settings, "authenticity_flag_threshold", 99)

    apply_authenticity(report, compute_authenticity(session, report))

    assert report.status == ReportStatus.FLAGGED


def test_scoring_never_rejects_a_report(session: Session) -> None:
    """FR-15 and TRD §10: rejection is a human action, never an automated one."""
    make(session, phash=HASH_A, client_minutes_ago=10, pseudonym="repeat")
    worst = make(
        session,
        phash=HASH_NEAR,
        lat=0.0,
        lng=0.0,
        text="help",
        pseudonym="repeat",
        client_minutes_ago=5,
        received_minutes_ago=-600,
    )

    assessment = compute_authenticity(session, worst)
    status = apply_authenticity(worst, assessment)

    assert assessment.score == 0
    assert status == ReportStatus.FLAGGED
    assert status != ReportStatus.REJECTED


def test_corroboration_can_offset_a_duplicate_penalty(session: Session) -> None:
    """Signals combine rather than short-circuit, and the arithmetic lands on the
    threshold boundary: 60 − 45 + 25 = 40, which is *not* below 40, so a resubmitted
    photograph backed by independent witnesses stays out of the review queue."""
    make(session, phash=HASH_A, received_minutes_ago=60, pseudonym="original-poster")
    for index in range(2):
        make(
            session,
            pseudonym=f"independent-witness-{index}",
            client_minutes_ago=4,
            received_minutes_ago=50,
        )

    reshare = make(
        session, phash=HASH_NEAR, pseudonym="resharer", client_minutes_ago=3, received_minutes_ago=30
    )

    assessment = compute_authenticity(session, reshare)
    status = apply_authenticity(reshare, assessment)

    assert {"DUPLICATE_IMAGE", "CORROBORATED"} <= set(codes(assessment))
    assert assessment.score == 40
    assert status == ReportStatus.VERIFIED


def test_scoring_never_deletes_a_report(session: Session) -> None:
    make(session, phash=HASH_A, received_minutes_ago=60)
    suspect = make(session, phash=HASH_NEAR, received_minutes_ago=30, pseudonym="reposter")
    suspect_id = suspect.id

    apply_authenticity(suspect, compute_authenticity(session, suspect))
    session.commit()

    assert session.get(Report, suspect_id) is not None


def test_an_already_verified_report_is_not_re_routed(session: Session) -> None:
    """Re-scoring must not silently undo a human's decision."""
    report = make(session, status=ReportStatus.VERIFIED, text="help")

    apply_authenticity(report, compute_authenticity(session, report))

    assert report.status == ReportStatus.VERIFIED
