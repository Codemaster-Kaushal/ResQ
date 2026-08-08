"""Phase 2: the data model behaves as the TRD specifies.

These run against their own throwaway engine so they cannot disturb, or be disturbed
by, the seeded dataset the other suites use.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401  — registers every table on the shared metadata
from app.core.time import minutes_between, to_naive_utc, utcnow
from app.models import (
    LEGAL_TRANSITIONS,
    Activity,
    Assignment,
    IncidentType,
    ProcessEvent,
    Report,
    ReportStatus,
    Responder,
    ResponderSkill,
    ResponderStatus,
    is_legal_transition,
)


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("models") / "models.db"
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with Session(engine) as db_session:
        yield db_session


def make_report(**overrides) -> Report:
    defaults = dict(
        idempotency_key=f"key-{uuid.uuid4()}",
        text="wall has collapsed, people trapped",
        lat=12.9352,
        lng=77.6245,
        client_created_at=datetime(2026, 8, 8, 9, 0),
        reporter_pseudonym="swift-heron-77",
    )
    defaults.update(overrides)
    return Report(**defaults)


# --- Schema ---------------------------------------------------------------------


def test_required_indexes_exist(engine) -> None:
    """TRD Phase 2 calls out these two by name — they carry the dedup lookups."""
    indexed_columns = {
        tuple(index["column_names"]) for index in inspect(engine).get_indexes("report")
    }

    assert ("idempotency_key",) in indexed_columns
    assert ("image_phash",) in indexed_columns


def test_idempotency_key_is_unique(session: Session) -> None:
    """The guarantee that makes offline batch re-sync a no-op (FR-27)."""
    session.add(make_report(idempotency_key="duplicate-key"))
    session.commit()

    session.add(make_report(idempotency_key="duplicate-key"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_process_event_uses_the_conventional_metadata_column(engine) -> None:
    """The attribute has to be renamed; the column must not be."""
    columns = {column["name"] for column in inspect(engine).get_columns("process_event")}

    assert "metadata" in columns
    assert "event_metadata" not in columns


# --- Values in the database ------------------------------------------------------


def test_enums_are_stored_as_their_lowercase_values(session: Session, engine) -> None:
    """SQLAlchemy defaults to persisting the member *name*; the API contract needs the
    value, so a regression here would silently desync the database from the JSON."""
    report = make_report(
        incident_type=IncidentType.STRUCTURAL_COLLAPSE,
        status=ReportStatus.CLASSIFIED,
    )
    session.add(report)
    session.commit()

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT status, incident_type FROM report WHERE id = :id"),
            {"id": str(report.id).replace("-", "")},
        ).first()

    assert stored == ("classified", "structural_collapse")


def test_json_reason_lists_round_trip(session: Session) -> None:
    reasons = [{"code": "LIFE_RISK_TRAPPED", "weight": 12, "source": "text"}]
    report = make_report(severity_reasons=reasons)
    session.add(report)
    session.commit()
    session.refresh(report)

    assert report.severity_reasons == reasons
    assert report.authenticity_reasons == []


def test_new_report_defaults_to_received_and_unscored(session: Session) -> None:
    report = make_report()
    session.add(report)
    session.commit()
    session.refresh(report)

    assert report.status == ReportStatus.RECEIVED
    assert report.severity_score is None
    assert report.authenticity_score is None
    assert report.scoring_provider is None
    assert report.manual_override_rank is None


# --- Relationships ----------------------------------------------------------------


def test_assignment_links_report_and_responder(session: Session) -> None:
    report = make_report()
    responder = Responder(
        name="Rescue Team Delta", skill=ResponderSkill.RESCUE, lat=12.97, lng=77.75, capacity=3
    )
    session.add(report)
    session.add(responder)
    session.commit()

    assignment = Assignment(report_id=report.id, responder_id=responder.id)
    session.add(assignment)
    session.commit()
    session.refresh(report)

    assert [a.id for a in report.assignments] == [assignment.id]
    assert assignment.responder.name == "Rescue Team Delta"
    assert assignment.report.id == report.id
    assert assignment.is_open


def test_assignment_stops_counting_once_resolved(session: Session) -> None:
    assignment = Assignment(report_id=uuid.uuid4(), responder_id=uuid.uuid4())
    assert assignment.is_open

    assignment.resolved_at = utcnow()
    assert not assignment.is_open


def test_responder_capacity_and_availability(session: Session) -> None:
    responder = Responder(
        name="Medical Unit Hotel",
        skill=ResponderSkill.MEDICAL,
        lat=12.969,
        lng=77.752,
        capacity=1,
        active_count=1,
    )

    assert not responder.has_spare_capacity
    assert not responder.is_dispatchable  # at capacity (FR-20)

    responder.active_count = 0
    assert responder.is_dispatchable

    responder.status = ResponderStatus.OFFLINE
    assert not responder.is_dispatchable


def test_process_event_metadata_round_trips(session: Session) -> None:
    report = make_report()
    session.add(report)
    session.commit()

    event = ProcessEvent(
        case_id=report.id,
        activity=Activity.REPORT_RECEIVED.value,
        resource="system",
        event_metadata={"source": "seed", "batch": 3},
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    assert event.event_metadata == {"source": "seed", "batch": 3}
    assert event.resource == "system"


# --- Lifecycle --------------------------------------------------------------------


def test_lifecycle_covers_every_status() -> None:
    assert set(LEGAL_TRANSITIONS) == set(ReportStatus)


def test_happy_path_is_walkable_end_to_end() -> None:
    path = [
        ReportStatus.RECEIVED,
        ReportStatus.CLASSIFIED,
        ReportStatus.VERIFIED,
        ReportStatus.QUEUED,
        ReportStatus.ASSIGNED,
        ReportStatus.ACKNOWLEDGED,
        ReportStatus.EN_ROUTE,
        ReportStatus.ON_SCENE,
        ReportStatus.RESOLVED,
        ReportStatus.CLOSED,
    ]

    for current, following in zip(path, path[1:]):
        assert is_legal_transition(current, following), f"{current} -> {following}"


def test_rejection_is_reachable_only_from_human_review() -> None:
    """No automated path may reject a report (FR-15, TRD §10)."""
    sources = [
        status
        for status, targets in LEGAL_TRANSITIONS.items()
        if ReportStatus.REJECTED in targets
    ]

    assert sources == [ReportStatus.FLAGGED]


def test_illegal_transitions_are_rejected() -> None:
    assert not is_legal_transition(ReportStatus.RECEIVED, ReportStatus.RESOLVED)
    assert not is_legal_transition(ReportStatus.CLOSED, ReportStatus.QUEUED)
    assert not is_legal_transition(ReportStatus.REJECTED, ReportStatus.VERIFIED)


def test_a_rejected_assignment_can_return_a_report_to_the_queue() -> None:
    """FR-21: the report goes back to the queue rather than dead-ending."""
    for status in (ReportStatus.ASSIGNED, ReportStatus.ACKNOWLEDGED, ReportStatus.EN_ROUTE):
        assert is_legal_transition(status, ReportStatus.QUEUED)


# --- Time -------------------------------------------------------------------------


def test_timestamps_are_naive_utc() -> None:
    """Mixing naive and aware datetimes is what breaks the ageing maths on SQLite."""
    assert utcnow().tzinfo is None

    from datetime import timedelta, timezone

    aware = datetime(2026, 8, 8, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert to_naive_utc(aware) == datetime(2026, 8, 8, 6, 30)
    assert to_naive_utc(aware).tzinfo is None


def test_minutes_between_is_signed() -> None:
    earlier = datetime(2026, 8, 8, 9, 0)
    later = datetime(2026, 8, 8, 9, 30)

    assert minutes_between(earlier, later) == 30
    assert minutes_between(later, earlier) == -30
