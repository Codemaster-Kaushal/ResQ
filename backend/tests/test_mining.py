"""Phase 9: cycle times and bottleneck detection (TRD §4.5, FR-24).

Acceptance: the bottleneck endpoint returns a real finding from seeded data, with a
suggested action.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401
from app.config import settings
from app.models import Activity, ProcessEvent, Report
from app.services.mining import analyse

NOW = datetime(2026, 8, 9, 12, 0, 0)
BOTTLENECKS = "/api/mining/bottlenecks"


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/mining.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with Session(engine) as db_session:
        yield db_session


def case(session: Session, *stages: tuple[Activity, float]) -> uuid.UUID:
    """Write one case's trail. Each stage is (activity, minutes before NOW).

    A real report is created first: case_id is a foreign key, so an invented one is
    rejected by the database.
    """
    report = Report(
        idempotency_key=f"m-{uuid.uuid4()}",
        text="a wall has collapsed",
        lat=12.9352,
        lng=77.6245,
        client_created_at=NOW - timedelta(hours=6),
        received_at=NOW - timedelta(hours=6),
        reporter_pseudonym="swift-heron-77",
    )
    session.add(report)
    session.commit()

    case_id = report.id
    for activity, minutes_ago in stages:
        session.add(
            ProcessEvent(
                case_id=case_id,
                activity=activity.value,
                resource="system",
                timestamp=NOW - timedelta(minutes=minutes_ago),
            )
        )
    session.commit()
    return case_id


def closed_case(session: Session, queued_ago: float, dispatch_minutes: float) -> uuid.UUID:
    """A case that ran all the way through, with a known QUEUED→ASSIGNED duration."""
    return case(
        session,
        (Activity.QUEUED, queued_ago),
        (Activity.ASSIGNED, queued_ago - dispatch_minutes),
        (Activity.ACKNOWLEDGED, queued_ago - dispatch_minutes - 2),
        (Activity.RESOLVED, queued_ago - dispatch_minutes - 20),
        (Activity.CLOSED, queued_ago - dispatch_minutes - 25),
    )


def stat_for(report, transition_from: str):
    return next(
        (stat for stat in report.transitions if stat.from_activity == transition_from), None
    )


# --- Medians ---------------------------------------------------------------------------


def test_the_median_is_learned_from_closed_cases(session: Session) -> None:
    for dispatch in (8.0, 10.0, 12.0):
        closed_case(session, queued_ago=300, dispatch_minutes=dispatch)

    report = analyse(session, NOW)

    stat = stat_for(report, Activity.QUEUED.value)
    assert report.closed_cases == 3
    assert stat.median_minutes == pytest.approx(10.0, abs=0.01)
    assert stat.transition == "QUEUED→ASSIGNED"


def test_the_median_resists_a_single_outlier(session: Session) -> None:
    """A median, not a mean, precisely so one nine-hour case cannot hide a problem."""
    for dispatch in (9.0, 10.0, 11.0, 540.0):
        closed_case(session, queued_ago=700, dispatch_minutes=dispatch)

    stat = stat_for(analyse(session, NOW), Activity.QUEUED.value)

    assert stat.median_minutes < 20


def test_open_cases_do_not_contribute_to_the_median(session: Session) -> None:
    closed_case(session, queued_ago=300, dispatch_minutes=10)
    case(session, (Activity.QUEUED, 400))  # still waiting, no ASSIGNED yet

    stat = stat_for(analyse(session, NOW), Activity.QUEUED.value)

    assert stat.closed_cases == 1
    assert stat.median_minutes == pytest.approx(10.0, abs=0.01)


# --- Detection --------------------------------------------------------------------------------


def test_a_slow_stage_is_flagged_with_an_action(session: Session) -> None:
    for dispatch in (9.0, 10.0, 11.0):
        closed_case(session, queued_ago=300, dispatch_minutes=dispatch)
    for waited in (90.0, 100.0, 110.0):
        case(session, (Activity.QUEUED, waited))

    report = analyse(session, NOW)

    assert len(report.bottlenecks) == 1
    finding = report.bottlenecks[0]
    assert finding.transition == "QUEUED→ASSIGNED"
    assert finding.median_minutes == pytest.approx(10.0, abs=0.01)
    assert finding.current_mean_minutes == pytest.approx(100.0, abs=0.01)
    assert finding.deviation_ratio == pytest.approx(10.0, abs=0.01)
    assert "responder" in finding.suggested_action.lower()


def test_a_healthy_stage_is_not_flagged(session: Session) -> None:
    for dispatch in (9.0, 10.0, 11.0):
        closed_case(session, queued_ago=300, dispatch_minutes=dispatch)
    case(session, (Activity.QUEUED, 11.0))  # within the usual range

    report = analyse(session, NOW)

    assert report.bottlenecks == []
    assert stat_for(report, Activity.QUEUED.value).is_bottleneck is False


def test_the_threshold_is_configurable(session: Session, monkeypatch) -> None:
    for dispatch in (10.0, 10.0, 10.0):
        closed_case(session, queued_ago=300, dispatch_minutes=dispatch)
    case(session, (Activity.QUEUED, 18.0))  # 1.8x the median

    assert analyse(session, NOW).bottlenecks  # flagged at the default 1.5

    monkeypatch.setattr(settings, "bottleneck_deviation_ratio", 5.0)
    assert analyse(session, NOW).bottlenecks == []


def test_bottlenecks_are_ranked_worst_first(session: Session) -> None:
    for dispatch in (10.0, 10.0):
        closed_case(session, queued_ago=300, dispatch_minutes=dispatch)
    # A slow acknowledge stage as well, but a milder one.
    for offset in (0.5, 1.5):
        case(
            session,
            (Activity.ASSIGNED, 200 + offset),
            (Activity.ACKNOWLEDGED, 190 + offset),
            (Activity.CLOSED, 100 + offset),
        )
    case(session, (Activity.QUEUED, 300.0))   # 30x
    case(session, (Activity.ASSIGNED, 25.0))  # ~2.5x

    ratios = [stat.deviation_ratio for stat in analyse(session, NOW).bottlenecks]

    assert ratios == sorted(ratios, reverse=True)


# --- Honest about missing data ----------------------------------------------------------------------


def test_no_history_means_no_finding_rather_than_a_wrong_one(session: Session) -> None:
    """With nothing completed there is no baseline, and inventing one would be worse
    than saying so."""
    case(session, (Activity.QUEUED, 500.0))

    report = analyse(session, NOW)

    assert report.closed_cases == 0
    assert report.bottlenecks == []
    assert report.note and "no closed cases" in report.note.lower()


def test_a_stage_with_no_baseline_is_reported_but_not_flagged(session: Session) -> None:
    """A long wait with nothing to compare it against is a fact, not a finding."""
    closed_case(session, queued_ago=300, dispatch_minutes=10)
    case(session, (Activity.REPORT_FLAGGED, 400.0))  # no closed case ever went via review

    report = analyse(session, NOW)

    flagged_stat = stat_for(report, Activity.REPORT_FLAGGED.value)
    assert flagged_stat.open_cases == 1
    assert flagged_stat.current_mean_minutes > 0
    assert flagged_stat.median_minutes == 0
    assert flagged_stat.is_bottleneck is False


def test_an_empty_log_does_not_crash(session: Session) -> None:
    report = analyse(session, NOW)

    assert report.closed_cases == 0
    assert report.open_cases == 0
    assert report.transitions == []


def test_a_rejected_case_counts_as_closed(session: Session) -> None:
    """Human rejection ends a case just as closure does."""
    case(
        session,
        (Activity.REPORT_FLAGGED, 100.0),
        (Activity.REPORT_REJECTED, 40.0),
    )

    report = analyse(session, NOW)

    assert report.closed_cases == 1
    assert stat_for(report, Activity.REPORT_FLAGGED.value).median_minutes == pytest.approx(60.0)


# --- Acceptance, through the API on seeded data --------------------------------------------------------


def test_the_endpoint_returns_a_real_finding(client: TestClient) -> None:
    """The seeded history exists so this is a measurement, not a mock."""
    body = client.get(BOTTLENECKS).json()

    assert body["closed_cases"] > 0, "the seed should carry some cases through to closed"
    assert body["open_cases"] > 0
    assert body["deviation_threshold"] == settings.bottleneck_deviation_ratio

    assert body["bottlenecks"], "seeded data should surface at least one slow stage"
    finding = body["bottlenecks"][0]
    assert finding["median_minutes"] > 0
    assert finding["current_mean_minutes"] > finding["median_minutes"]
    assert finding["deviation_ratio"] > settings.bottleneck_deviation_ratio
    assert finding["suggested_action"]


def test_the_endpoint_reports_every_stage_not_only_the_slow_ones(client: TestClient) -> None:
    body = client.get(BOTTLENECKS).json()

    transitions = {item["transition"] for item in body["transitions"]}
    assert len(transitions) > len(body["bottlenecks"])
    assert any("QUEUED" in name for name in transitions)


def test_the_dispatch_wait_is_the_headline_finding(client: TestClient) -> None:
    """On the seeded dataset the queue is the stage under strain — which is the point:
    it is where a mass-casualty surge actually shows up."""
    body = client.get(BOTTLENECKS).json()

    assert body["bottlenecks"][0]["from_activity"] == Activity.QUEUED.value
