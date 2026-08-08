"""Phase 7: responder matching (TRD §4.4).

Per TRD §8: the capacity limit, skill preference over pure proximity, and the
no-candidate path.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.models  # noqa: F401
from app.config import settings
from app.core.geo import offset_metres
from app.models import (
    Activity,
    Assignment,
    IncidentType,
    ProcessEvent,
    Report,
    ReportStatus,
    Responder,
    ResponderSkill,
    ResponderStatus,
)
from app.services.dispatch import (
    COMPATIBLE_SKILL,
    EXACT_SKILL,
    MISMATCHED_SKILL,
    DispatchOutcome,
    assign_report,
    dispatch_batch,
    dispatch_next,
    find_candidates,
    required_skill,
    score_match,
    skill_component,
)

NOW = datetime(2026, 8, 9, 12, 0, 0)
BASE_LAT, BASE_LNG = 12.9352, 77.6245


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/dispatch.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with Session(engine) as db_session:
        yield db_session


def a_report(
    session: Session,
    *,
    key: str | None = None,
    incident: IncidentType = IncidentType.MEDICAL,
    severity: int = 60,
    north_m: float = 0,
    east_m: float = 0,
    minutes_ago: float = 10,
    status: ReportStatus = ReportStatus.QUEUED,
) -> Report:
    lat, lng = offset_metres(BASE_LAT, BASE_LNG, north_m, east_m)
    report = Report(
        idempotency_key=key or f"d-{uuid.uuid4()}",
        text="a person has collapsed and is unconscious",
        lat=lat,
        lng=lng,
        client_created_at=NOW - timedelta(minutes=minutes_ago),
        received_at=NOW - timedelta(minutes=minutes_ago),
        reporter_pseudonym="swift-heron-77",
        incident_type=incident,
        severity_score=severity,
        severity_reasons=[{"code": "X", "weight": severity, "source": "taxonomy"}],
        authenticity_score=60,
        authenticity_reasons=[{"code": "BASELINE", "weight": 60, "source": "system"}],
        status=status,
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def a_responder(
    session: Session,
    *,
    name: str,
    skill: ResponderSkill = ResponderSkill.MEDICAL,
    north_m: float = 0,
    east_m: float = 0,
    capacity: int = 2,
    active: int = 0,
    status: ResponderStatus = ResponderStatus.AVAILABLE,
) -> Responder:
    lat, lng = offset_metres(BASE_LAT, BASE_LNG, north_m, east_m)
    responder = Responder(
        name=name, skill=skill, lat=lat, lng=lng, capacity=capacity, active_count=active, status=status
    )
    session.add(responder)
    session.commit()
    session.refresh(responder)
    return responder


def names(candidates) -> list[str]:
    return [responder.name for responder, _ in candidates]


# --- Skill mapping ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("incident", "skill"),
    [
        (IncidentType.MEDICAL, ResponderSkill.MEDICAL),
        (IncidentType.TRAPPED_PERSONS, ResponderSkill.RESCUE),
        (IncidentType.STRUCTURAL_COLLAPSE, ResponderSkill.RESCUE),
        (IncidentType.FIRE, ResponderSkill.RESCUE),
        (IncidentType.FLOODING, ResponderSkill.STRUCTURAL),
        (IncidentType.LANDSLIDE, ResponderSkill.STRUCTURAL),
        (IncidentType.INFRASTRUCTURE, ResponderSkill.STRUCTURAL),
    ],
)
def test_incident_to_skill_mapping(incident: IncidentType, skill: ResponderSkill) -> None:
    assert required_skill(incident) == skill


def test_an_unclassified_incident_still_gets_a_crew() -> None:
    """`other` is outside the TRD's mapping; it must not mean "nobody goes"."""
    assert required_skill(IncidentType.OTHER) == ResponderSkill.RESCUE
    assert required_skill(None) == ResponderSkill.RESCUE


def test_skill_components() -> None:
    assert skill_component(ResponderSkill.MEDICAL, ResponderSkill.MEDICAL) == EXACT_SKILL
    assert skill_component(ResponderSkill.MEDICAL, ResponderSkill.RESCUE) == COMPATIBLE_SKILL
    assert skill_component(ResponderSkill.MEDICAL, ResponderSkill.STRUCTURAL) == MISMATCHED_SKILL
    assert skill_component(ResponderSkill.RESCUE, ResponderSkill.STRUCTURAL) == COMPATIBLE_SKILL


# --- Scoring ------------------------------------------------------------------------------


def test_match_score_follows_the_formula(session: Session) -> None:
    report = a_report(session, incident=IncidentType.MEDICAL)
    responder = a_responder(session, name="Alpha", skill=ResponderSkill.MEDICAL, capacity=2, active=1)

    match = score_match(report, responder)

    # co-located: 0.5*1.0 + 0.3*1.0 + 0.2*0.5
    assert match.distance_component == pytest.approx(1.0, abs=1e-3)
    assert match.skill_component == 1.0
    assert match.load_component == pytest.approx(0.5)
    assert match.score == pytest.approx(0.9, abs=1e-3)


def test_distance_lowers_the_score(session: Session) -> None:
    report = a_report(session)
    near = a_responder(session, name="Near", east_m=100)
    far = a_responder(session, name="Far", east_m=10_000)

    assert score_match(report, near).score > score_match(report, far).score


def test_a_loaded_responder_scores_below_an_idle_one(session: Session) -> None:
    report = a_report(session)
    idle = a_responder(session, name="Idle", capacity=4, active=0)
    loaded = a_responder(session, name="Loaded", capacity=4, active=3)

    assert score_match(report, idle).score > score_match(report, loaded).score


# --- Acceptance: skill beats proximity --------------------------------------------------------


def test_the_skill_matched_unit_wins_over_the_merely_closest(session: Session) -> None:
    """The whole premise of G3: the best-fit responder, not the first free one."""
    report = a_report(session, incident=IncidentType.MEDICAL)
    a_responder(session, name="Structural (closest)", skill=ResponderSkill.STRUCTURAL, east_m=50)
    a_responder(session, name="Medical (further)", skill=ResponderSkill.MEDICAL, east_m=600)

    responder, match = find_candidates(session, report)[0]

    assert responder.name == "Medical (further)"
    assert match.skill_component == EXACT_SKILL


def test_proximity_still_wins_when_skills_are_equal(session: Session) -> None:
    """Skill is a tiebreaker on top of distance, not a replacement for it."""
    report = a_report(session, incident=IncidentType.MEDICAL)
    a_responder(session, name="Near medical", skill=ResponderSkill.MEDICAL, east_m=100)
    a_responder(session, name="Far medical", skill=ResponderSkill.MEDICAL, east_m=8_000)

    assert names(find_candidates(session, report))[0] == "Near medical"


def test_distance_can_outweigh_skill_at_the_extremes(session: Session) -> None:
    """0.3 of skill cannot beat 0.5 of distance across the whole radius — a matched
    unit 24 km away should not be sent past a compatible one next door."""
    report = a_report(session, incident=IncidentType.MEDICAL)
    a_responder(session, name="Compatible next door", skill=ResponderSkill.RESCUE, east_m=50)
    a_responder(session, name="Matched far away", skill=ResponderSkill.MEDICAL, east_m=24_000)

    assert names(find_candidates(session, report))[0] == "Compatible next door"


# --- Acceptance: capacity and availability -------------------------------------------------------


def test_a_responder_at_capacity_is_never_a_candidate(session: Session) -> None:
    """FR-20: capacity is never exceeded."""
    report = a_report(session)
    a_responder(session, name="Full", capacity=1, active=1)

    assert find_candidates(session, report) == []


def test_an_offline_responder_is_never_a_candidate(session: Session) -> None:
    report = a_report(session)
    a_responder(session, name="Offline", capacity=5, status=ResponderStatus.OFFLINE)

    assert find_candidates(session, report) == []


def test_a_busy_responder_is_never_a_candidate(session: Session) -> None:
    report = a_report(session)
    a_responder(session, name="Busy", capacity=5, active=1, status=ResponderStatus.BUSY)

    assert find_candidates(session, report) == []


def test_a_responder_beyond_the_radius_is_excluded(session: Session) -> None:
    report = a_report(session)
    a_responder(session, name="Too far", east_m=30_000)

    assert find_candidates(session, report) == []


def test_the_radius_is_configurable(session: Session, monkeypatch) -> None:
    report = a_report(session)
    a_responder(session, name="Distant", east_m=30_000)

    monkeypatch.setattr(settings, "dispatch_max_radius_km", 50)

    assert names(find_candidates(session, report)) == ["Distant"]


def test_assignment_consumes_capacity_and_marks_a_full_unit_busy(session: Session) -> None:
    report = a_report(session)
    responder = a_responder(session, name="Solo", capacity=1)

    assign_report(session, report)
    session.commit()
    session.refresh(responder)

    assert responder.active_count == 1
    assert responder.status == ResponderStatus.BUSY


def test_capacity_is_never_exceeded_under_repeated_dispatch(session: Session) -> None:
    for index in range(6):
        a_report(session, key=f"load-{index}", severity=90 - index)
    a_responder(session, name="Small team", capacity=2)

    dispatch_batch(session, limit=6)
    session.commit()

    responder = session.exec(select(Responder)).one()
    open_assignments = [a for a in session.exec(select(Assignment)).all() if a.is_open]

    assert responder.active_count == 2
    assert len(open_assignments) == 2
    assert responder.active_count <= responder.capacity


# --- Acceptance: the deferred path --------------------------------------------------------------


def test_no_candidate_leaves_the_report_queued(session: Session) -> None:
    """A report is never dropped for want of a responder."""
    report = a_report(session)
    a_responder(session, name="Offline", status=ResponderStatus.OFFLINE)

    result = assign_report(session, report)
    session.commit()
    session.refresh(report)

    assert result.outcome == DispatchOutcome.DEFERRED
    assert report.status == ReportStatus.QUEUED
    assert session.exec(select(Assignment)).all() == []


def test_deferral_emits_an_event(session: Session) -> None:
    report = a_report(session)

    assign_report(session, report)
    session.commit()

    events = session.exec(
        select(ProcessEvent).where(ProcessEvent.activity == Activity.DISPATCH_DEFERRED.value)
    ).all()

    assert len(events) == 1
    assert events[0].case_id == report.id
    assert events[0].event_metadata["required_skill"] == "medical"


def test_a_deferred_report_keeps_its_accrued_wait(session: Session) -> None:
    """FR-21's sibling: waiting time survives a failed dispatch attempt."""
    report = a_report(session, minutes_ago=90)
    filed_at = report.client_created_at

    assign_report(session, report)
    session.commit()
    session.refresh(report)

    assert report.client_created_at == filed_at


def test_only_the_head_of_the_queue_records_a_deferral(session: Session) -> None:
    """Otherwise an exhausted fleet buries the log in identical events."""
    for index in range(5):
        a_report(session, key=f"nobody-{index}", severity=90 - index)

    dispatch_next(session)
    session.commit()

    events = session.exec(
        select(ProcessEvent).where(ProcessEvent.activity == Activity.DISPATCH_DEFERRED.value)
    ).all()

    assert len(events) == 1


def test_dispatch_looks_past_an_unplaceable_head(session: Session) -> None:
    """A crew that could be helping should not idle because the top report is remote."""
    a_report(session, key="remote-worst", severity=99, east_m=40_000)
    a_report(session, key="local-lesser", severity=50)
    a_responder(session, name="Local unit")

    result = dispatch_next(session)
    session.commit()

    assert result.outcome == DispatchOutcome.ASSIGNED
    assert result.report.idempotency_key == "local-lesser"


def test_an_empty_queue_is_not_an_error(session: Session) -> None:
    a_responder(session, name="Idle unit")

    assert dispatch_next(session).outcome == DispatchOutcome.QUEUE_EMPTY


# --- Assignment bookkeeping -------------------------------------------------------------------------


def test_assignment_records_both_sides(session: Session) -> None:
    report = a_report(session)
    responder = a_responder(session, name="Alpha")

    result = assign_report(session, report)
    session.commit()

    assignment = session.exec(select(Assignment)).one()
    assert assignment.report_id == report.id
    assert assignment.responder_id == responder.id
    assert assignment.is_open
    assert result.match.score > 0


def test_assignment_moves_the_report_out_of_the_queue(session: Session) -> None:
    report = a_report(session)
    a_responder(session, name="Alpha")

    assign_report(session, report)
    session.commit()
    session.refresh(report)

    assert report.status == ReportStatus.ASSIGNED


def test_assignment_emits_an_event_carrying_the_match(session: Session) -> None:
    report = a_report(session)
    a_responder(session, name="Alpha")

    assign_report(session, report, operator="controller-meera")
    session.commit()

    event = session.exec(
        select(ProcessEvent).where(ProcessEvent.activity == Activity.ASSIGNED.value)
    ).one()

    assert event.resource == "operator:controller-meera"
    assert event.event_metadata["responder_name"] == "Alpha"
    assert event.event_metadata["match"]["required_skill"] == "medical"


def test_dispatch_takes_the_highest_priority_report_first(session: Session) -> None:
    a_report(session, key="minor", severity=15)
    a_report(session, key="critical", severity=95)
    a_responder(session, name="Alpha", capacity=1)

    result = dispatch_next(session)
    session.commit()

    assert result.report.idempotency_key == "critical"


def test_candidate_ordering_is_reproducible(session: Session) -> None:
    report = a_report(session)
    for index in range(4):
        a_responder(session, name=f"Unit {index}")

    assert names(find_candidates(session, report)) == names(find_candidates(session, report))
