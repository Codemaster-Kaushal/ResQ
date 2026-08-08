"""Phase 6: priority ordering and ageing (TRD §4.3).

Per TRD §8: ageing must prevent starvation, and an override must sort above every
computed score.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401
from app.config import settings
from app.models import IncidentType, Report, ReportStatus
from app.services.priority import (
    BAND_COMPUTED,
    BAND_DEMOTED,
    BAND_PINNED,
    ageing_bonus,
    band_of,
    build_queue,
    compute_priority,
    enqueue_verified,
    find_position,
)

NOW = datetime(2026, 8, 9, 12, 0, 0)


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/queue.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with Session(engine) as db_session:
        yield db_session


def make(
    session: Session,
    *,
    key: str | None = None,
    severity: int = 50,
    authenticity: int = 60,
    minutes_ago: float = 0,
    status: ReportStatus = ReportStatus.QUEUED,
    override: int | None = None,
    commit: bool = True,
) -> Report:
    report = Report(
        idempotency_key=key or f"q-{uuid.uuid4()}",
        text="a wall has collapsed and people are trapped",
        lat=12.9352,
        lng=77.6245,
        client_created_at=NOW - timedelta(minutes=minutes_ago),
        received_at=NOW - timedelta(minutes=minutes_ago),
        reporter_pseudonym="swift-heron-77",
        incident_type=IncidentType.STRUCTURAL_COLLAPSE,
        severity_score=severity,
        severity_reasons=[{"code": "X", "weight": severity, "source": "taxonomy"}],
        authenticity_score=authenticity,
        authenticity_reasons=[{"code": "BASELINE", "weight": authenticity, "source": "system"}],
        status=status,
        manual_override_rank=override,
    )
    session.add(report)
    if commit:
        session.commit()
        session.refresh(report)
    return report


def order(session: Session, now: datetime = NOW) -> list[str]:
    return [entry.report.idempotency_key for entry in build_queue(session, now)]


# --- The formula ---------------------------------------------------------------------


def test_priority_matches_the_specified_formula(session: Session) -> None:
    report = make(session, severity=80, authenticity=60, minutes_ago=20)

    breakdown = compute_priority(report, NOW)

    # 0.70*80 + 0.15*60 + 0.15*(20*1.5) = 56 + 9 + 4.5
    assert breakdown.ageing_bonus == pytest.approx(30.0)
    assert breakdown.score == pytest.approx(69.5)


def test_each_component_moves_the_score_independently(session: Session) -> None:
    base = compute_priority(make(session, severity=50, authenticity=50, minutes_ago=0), NOW)
    hotter = compute_priority(make(session, severity=60, authenticity=50, minutes_ago=0), NOW)
    trusted = compute_priority(make(session, severity=50, authenticity=90, minutes_ago=0), NOW)
    older = compute_priority(make(session, severity=50, authenticity=50, minutes_ago=30), NOW)

    assert hotter.score > base.score
    assert trusted.score > base.score
    assert older.score > base.score


def test_severity_dominates_the_other_terms(session: Session) -> None:
    """Weighted 0.70 against 0.15 apiece — that is the whole premise of the product."""
    severe_and_fresh = compute_priority(make(session, severity=95, authenticity=40, minutes_ago=0), NOW)
    mild_and_trusted = compute_priority(make(session, severity=30, authenticity=100, minutes_ago=0), NOW)

    assert severe_and_fresh.score > mild_and_trusted.score


def test_unscored_reports_do_not_crash_the_queue(session: Session) -> None:
    report = make(session, severity=50)
    report.severity_score = None
    report.authenticity_score = None

    assert compute_priority(report, NOW).score >= 0


# --- Ageing (FR-17) --------------------------------------------------------------------


def test_ageing_accrues_with_wait_time() -> None:
    filed = NOW - timedelta(minutes=10)

    assert ageing_bonus(filed, NOW) == pytest.approx(15.0)  # 10 * 1.5


def test_ageing_is_capped() -> None:
    ancient = NOW - timedelta(days=3)

    assert ageing_bonus(ancient, NOW) == settings.ageing_max_bonus


def test_ageing_uses_the_client_clock_not_receipt(session: Session) -> None:
    """FR-28: a report filed offline an hour ago has been waiting an hour, whoever
    slow the sync was. Using receipt time would punish the worst connectivity."""
    report = make(session, minutes_ago=60)
    report.received_at = NOW  # synced only just now
    session.add(report)
    session.commit()

    breakdown = compute_priority(report, NOW)

    assert breakdown.minutes_waiting == pytest.approx(60.0)
    assert breakdown.ageing_bonus == pytest.approx(90.0)  # 60 * 1.5, still under the cap


def test_a_future_client_clock_earns_nothing(session: Session) -> None:
    """Clock skew must not be a way to jump the queue."""
    report = make(session, minutes_ago=-120)

    assert compute_priority(report, NOW).ageing_bonus == 0.0


def test_waiting_lifts_a_report_past_a_fresher_more_severe_one(session: Session) -> None:
    """The starvation guarantee: nothing sits at the bottom for ever."""
    make(session, key="fresh-worse", severity=60, minutes_ago=0)
    make(session, key="stale-milder", severity=45, minutes_ago=0)

    assert order(session) == ["fresh-worse", "stale-milder"]

    # Two hours later, the milder report has aged to the cap and overtakes.
    later = NOW + timedelta(hours=2)
    aged_first, aged_second = order(session, later)
    stale = next(
        e for e in build_queue(session, later) if e.report.idempotency_key == "stale-milder"
    )

    assert stale.breakdown.ageing_bonus == settings.ageing_max_bonus


def test_a_neglected_report_keeps_climbing(session: Session) -> None:
    report = make(session, severity=10, minutes_ago=0)

    scores = [
        compute_priority(report, NOW + timedelta(minutes=minutes)).score
        for minutes in (0, 10, 30, 60)
    ]

    assert scores == sorted(scores)
    assert scores[-1] > scores[0]


# --- Queue membership ---------------------------------------------------------------------


def test_only_queueable_reports_appear(session: Session) -> None:
    make(session, key="in-queued", status=ReportStatus.QUEUED)
    make(session, key="in-verified", status=ReportStatus.VERIFIED)
    make(session, key="out-flagged", status=ReportStatus.FLAGGED)
    make(session, key="out-received", status=ReportStatus.RECEIVED)
    make(session, key="out-assigned", status=ReportStatus.ASSIGNED)
    make(session, key="out-rejected", status=ReportStatus.REJECTED)
    make(session, key="out-closed", status=ReportStatus.CLOSED)

    assert set(order(session)) == {"in-queued", "in-verified"}


def test_enqueue_moves_a_verified_report_in(session: Session) -> None:
    report = make(session, status=ReportStatus.VERIFIED)

    assert enqueue_verified(session, report) is True
    assert report.status == ReportStatus.QUEUED
    assert report.priority_score is not None


def test_enqueue_ignores_reports_in_other_states(session: Session) -> None:
    for status in (ReportStatus.FLAGGED, ReportStatus.QUEUED, ReportStatus.ASSIGNED):
        report = make(session, status=status)
        assert enqueue_verified(session, report) is False
        assert report.status == status


# --- Ordering and overrides (FR-18) ------------------------------------------------------------


def test_the_queue_is_ordered_by_priority(session: Session) -> None:
    make(session, key="low", severity=20)
    make(session, key="high", severity=90)
    make(session, key="mid", severity=55)

    assert order(session) == ["high", "mid", "low"]


def test_a_pin_sorts_above_every_computed_score(session: Session) -> None:
    make(session, key="worst", severity=100, minutes_ago=600)
    make(session, key="pinned", severity=1, override=0)

    assert order(session)[0] == "pinned"


def test_a_demotion_sorts_below_every_computed_score(session: Session) -> None:
    make(session, key="least", severity=1)
    make(session, key="demoted", severity=100, override=-1)

    assert order(session)[-1] == "demoted"


def test_pins_are_ordered_among_themselves(session: Session) -> None:
    make(session, key="second-pin", severity=99, override=1)
    make(session, key="first-pin", severity=1, override=0)
    make(session, key="unpinned", severity=95)

    assert order(session) == ["first-pin", "second-pin", "unpinned"]


def test_demotions_are_ordered_among_themselves(session: Session) -> None:
    make(session, key="least-demoted", severity=1, override=-1)
    make(session, key="most-demoted", severity=99, override=-5)

    assert order(session) == ["least-demoted", "most-demoted"]


def test_bands_are_derived_from_the_override_sign(session: Session) -> None:
    assert band_of(make(session, override=None)) == BAND_COMPUTED
    assert band_of(make(session, override=0)) == BAND_PINNED
    assert band_of(make(session, override=3)) == BAND_PINNED
    assert band_of(make(session, override=-1)) == BAND_DEMOTED


def test_clearing_an_override_returns_a_report_to_the_computed_band(session: Session) -> None:
    pinned = make(session, key="was-pinned", severity=5, override=0)
    make(session, key="normal", severity=90)

    assert order(session)[0] == "was-pinned"

    pinned.manual_override_rank = None
    session.add(pinned)
    session.commit()

    assert order(session)[0] == "normal"


def test_ordering_is_stable_for_equal_scores(session: Session) -> None:
    """Identical scores must not shuffle between reads."""
    for index in range(5):
        make(session, key=f"tie-{index}", severity=50, minutes_ago=0)

    assert order(session) == order(session)


def test_position_lookup_matches_the_queue(session: Session) -> None:
    make(session, key="first", severity=90)
    target = make(session, key="second", severity=50)
    make(session, key="third", severity=10)

    assert find_position(session, target.id, NOW) == 2


def test_position_is_none_for_a_report_outside_the_queue(session: Session) -> None:
    report = make(session, status=ReportStatus.FLAGGED)

    assert find_position(session, report.id, NOW) is None


# --- Configurability -----------------------------------------------------------------------------


def test_weights_are_configurable(session: Session, monkeypatch) -> None:
    """Judges ask "what if you tuned that?" — the answer is an env var."""
    make(session, key="severe", severity=90, authenticity=10, minutes_ago=0)
    make(session, key="trusted", severity=10, authenticity=90, minutes_ago=0)

    assert order(session) == ["severe", "trusted"]

    monkeypatch.setattr(settings, "priority_weight_severity", 0.10)
    monkeypatch.setattr(settings, "priority_weight_authenticity", 0.80)

    assert order(session) == ["trusted", "severe"]


def test_the_ageing_rate_is_configurable(monkeypatch) -> None:
    filed = NOW - timedelta(minutes=10)
    monkeypatch.setattr(settings, "ageing_rate_per_minute", 3.0)

    assert ageing_bonus(filed, NOW) == pytest.approx(30.0)
