"""Phase 2 acceptance: the seed is idempotent and its fixtures are real.

`python -m seed.seed` must run twice with no duplicates and no errors, and the row
counts must assert correctly.
"""

from __future__ import annotations

import time

import pytest
from sqlmodel import Session, func, select

from app.config import settings
from app.core.geo import haversine_m
from app.core.time import minutes_between, utcnow
from app.db import engine
from app.models import Report, ReportStatus, Responder
from seed import images, seed as seed_module
from seed.fixtures.reports import EXPECTED_REPORT_COUNT, REPORT_SPECS
from seed.fixtures.responders import RESPONDER_SPECS


def key(name: str) -> str:
    return f"{seed_module.IDEMPOTENCY_PREFIX}{name}"


@pytest.fixture(scope="module")
def seeded() -> seed_module.SeedSummary:
    """One clean seed run for the whole module."""
    return seed_module.run(reset=True, echo=lambda _line: None)


@pytest.fixture
def session():
    with Session(engine) as db_session:
        yield db_session


def fetch(session: Session, name: str) -> Report | None:
    return session.exec(select(Report).where(Report.idempotency_key == key(name))).first()


# --- Acceptance -------------------------------------------------------------------


def test_seed_reports_all_checks_passing(seeded: seed_module.SeedSummary) -> None:
    failures = [f"{check.name}: {check.detail}" for check in seeded.checks if not check.passed]

    assert not failures, "seed self-verification failed:\n" + "\n".join(failures)


def test_first_run_creates_the_expected_rows(seeded: seed_module.SeedSummary) -> None:
    assert seeded.reports_created == EXPECTED_REPORT_COUNT
    assert seeded.responders_created == len(RESPONDER_SPECS)


def test_running_again_is_a_no_op(seeded: seed_module.SeedSummary, session: Session) -> None:
    """The acceptance criterion: run it twice, get no duplicates and no errors."""
    before = session.exec(select(func.count()).select_from(Report)).one()

    second = seed_module.run(reset=False, echo=lambda _line: None)

    assert second.reports_created == 0
    assert second.responders_created == 0
    assert second.reports_existing == EXPECTED_REPORT_COUNT
    assert second.ok
    assert session.exec(select(func.count()).select_from(Report)).one() == before


def test_row_counts_assert_correctly(seeded, session: Session) -> None:
    assert session.exec(select(func.count()).select_from(Report)).one() == EXPECTED_REPORT_COUNT
    assert session.exec(select(func.count()).select_from(Responder)).one() == len(RESPONDER_SPECS)

    distinct_keys = session.exec(
        select(func.count(func.distinct(Report.idempotency_key)))
    ).one()
    assert distinct_keys == EXPECTED_REPORT_COUNT


def test_ids_are_deterministic_across_runs() -> None:
    """Reproducibility (NFR-4) rests on this: same fixture key, same UUID, any machine."""
    assert seed_module.stable_id("report", "dup-image-a") == seed_module.stable_id(
        "report", "dup-image-a"
    )
    assert seed_module.stable_id("report", "dup-image-a") != seed_module.stable_id(
        "responder", "dup-image-a"
    )


def test_seed_loads_well_inside_the_budget() -> None:
    """NFR-4 allows ten seconds."""
    started = time.perf_counter()
    seed_module.run(reset=True, echo=lambda _line: None)
    elapsed = time.perf_counter() - started

    assert elapsed < 10.0, f"seed took {elapsed:.1f}s"


# --- Deliberate fixtures ----------------------------------------------------------


def test_duplicate_image_pair_is_within_the_phash_threshold(seeded, session: Session) -> None:
    first, second = fetch(session, "dup-image-a"), fetch(session, "dup-image-b")

    assert first and second
    assert first.image_phash and second.image_phash

    distance = images.hamming(first.image_phash, second.image_phash)
    assert distance <= settings.phash_duplicate_distance

    # Two different people forwarding one photograph — not one person posting twice.
    assert first.reporter_pseudonym != second.reporter_pseudonym


def test_unrelated_images_are_not_near_duplicates(seeded, session: Session) -> None:
    """Otherwise Phase 5 would flag honest reports."""
    hashed = session.exec(select(Report).where(Report.image_phash.is_not(None))).all()
    duplicate_pair = {key("dup-image-a"), key("dup-image-b")}

    for index, left in enumerate(hashed):
        for right in hashed[index + 1 :]:
            if {left.idempotency_key, right.idempotency_key} == duplicate_pair:
                continue
            distance = images.hamming(left.image_phash, right.image_phash)
            assert distance > settings.phash_duplicate_distance, (
                f"{left.idempotency_key} and {right.idempotency_key} collide at {distance}"
            )


def test_image_files_exist_on_disk_with_stored_paths(seeded, session: Session) -> None:
    with_images = session.exec(select(Report).where(Report.image_path.is_not(None))).all()

    assert with_images
    for report in with_images:
        # Paths are stored relative to the media root so the database stays portable.
        assert not report.image_path.startswith("/")
        assert (settings.media_dir / report.image_path).is_file()


def test_stale_report_predates_its_receipt(seeded, session: Session) -> None:
    stale = fetch(session, "stale-timestamp")

    assert stale
    hours = minutes_between(stale.client_created_at, stale.received_at) / 60
    assert hours > 6  # TRD §4.2 STALE_REPORT threshold


def test_newest_report_is_the_critical_one(seeded, session: Session) -> None:
    """Phase 6 has to show severity beating arrival order — this is the report that
    proves it, and it only proves anything if it really is the newest."""
    newest = session.exec(
        select(Report).order_by(Report.client_created_at.desc())
    ).first()

    assert newest and newest.idempotency_key == key("latest-critical")


def test_corroboration_cluster_sits_inside_the_window(seeded, session: Session) -> None:
    cluster = [fetch(session, f"corroborated-{n}") for n in (1, 2, 3)]

    assert all(cluster)
    for index, left in enumerate(cluster):
        for right in cluster[index + 1 :]:
            assert haversine_m(left.lat, left.lng, right.lat, right.lng) <= (
                settings.corroboration_radius_m
            )
            gap = abs(minutes_between(left.client_created_at, right.client_created_at))
            assert gap <= settings.corroboration_window_min

    # Independent reporters, or it is not corroboration.
    assert len({report.reporter_pseudonym for report in cluster}) == 3


def test_impossible_movement_pair(seeded, session: Session) -> None:
    first, second = fetch(session, "impossible-move-a"), fetch(session, "impossible-move-b")

    assert first and second
    assert first.reporter_pseudonym == second.reporter_pseudonym
    assert haversine_m(first.lat, first.lng, second.lat, second.lng) / 1000 > 100
    assert abs(minutes_between(first.client_created_at, second.client_created_at)) <= 10


def test_null_island_report_exists(seeded, session: Session) -> None:
    report = fetch(session, "null-island")

    assert report and report.lat == 0.0 and report.lng == 0.0


def test_low_information_report_is_short(seeded, session: Session) -> None:
    report = fetch(session, "low-information")

    assert report and len(report.text.split()) < 5


def test_aged_low_severity_report_has_been_waiting(seeded, session: Session) -> None:
    report = fetch(session, "aged-low-severity")

    assert report
    assert minutes_between(report.client_created_at, utcnow()) > 180


# --- Dataset shape ----------------------------------------------------------------


def test_every_seeded_report_is_scored_and_explained(seeded, session: Session) -> None:
    """Phase 4 acceptance: every seeded report has a severity score and non-empty
    reasons. Scores are computed by the engine at seed time, never seeded directly."""
    for report in session.exec(select(Report)).all():
        # The pipeline routes every report to the queue or to human review; the seeded
        # history then carries a handful of the oldest ones through to `closed`, so the
        # bottleneck baseline has completed cases to learn from.
        assert report.status in {
            ReportStatus.QUEUED,
            ReportStatus.FLAGGED,
            ReportStatus.CLOSED,
        }, report.idempotency_key
        assert report.severity_score is not None, report.idempotency_key
        assert 0 <= report.severity_score <= 100
        assert report.severity_reasons, report.idempotency_key
        assert report.incident_type is not None
        assert report.scoring_provider == "local"

        assert report.authenticity_score is not None, report.idempotency_key
        assert 0 <= report.authenticity_score <= 100
        assert report.authenticity_reasons, report.idempotency_key

        # Anything that reached the queue carries a priority score, including the
        # cases that have since been closed. Flagged reports never entered it.
        if report.status == ReportStatus.FLAGGED:
            assert report.priority_score is None
        else:
            assert report.priority_score is not None, report.idempotency_key


def test_seeded_reasons_reconcile_with_their_scores(seeded, session: Session) -> None:
    """A score you cannot add up from its reasons is not explained (FR-8)."""
    for report in session.exec(select(Report)).all():
        total = sum(item["weight"] for item in report.severity_reasons)
        assert total == report.severity_score, report.idempotency_key


def test_seeding_offline_still_scores_everything(seeded, session: Session) -> None:
    """NFR-2: the seed run above had no API keys configured, so the remote providers
    were skipped and the local scorer answered — which is the offline path exactly."""
    providers = {r.scoring_provider for r in session.exec(select(Report)).all()}

    assert providers == {"local"}


def test_every_spec_reached_the_database(seeded, session: Session) -> None:
    stored = {report.idempotency_key for report in session.exec(select(Report)).all()}
    expected = {key(spec.key) for spec in REPORT_SPECS}

    assert stored == expected


def test_responder_fixtures_cover_the_dispatch_edge_cases(seeded, session: Session) -> None:
    responders = {r.name: r for r in session.exec(select(Responder)).all()}

    assert not responders["Medical Unit Hotel"].is_dispatchable  # at capacity
    assert not responders["Rescue Team Golf"].is_dispatchable  # offline
    assert responders["Medical Unit Alpha"].is_dispatchable

    # Phase 7 needs a case where the nearest unit has the wrong skill.
    alpha = responders["Medical Unit Alpha"]
    echo = responders["Structural Crew Echo"]
    incident = next(r for r in session.exec(select(Report)).all() if r.idempotency_key == key("impossible-move-a"))
    assert haversine_m(incident.lat, incident.lng, echo.lat, echo.lng) < haversine_m(
        incident.lat, incident.lng, alpha.lat, alpha.lng
    )
