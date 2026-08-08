"""Phase 7 through the API: POST /api/dispatch/assign."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.models import (
    Activity,
    Assignment,
    ProcessEvent,
    Report,
    ReportStatus,
    Responder,
    ResponderStatus,
)
from app.services.priority import build_queue
from seed import seed as seed_module
from seed.fixtures.responders import RESPONDER_SPECS

ASSIGN = "/api/dispatch/assign"


@pytest.fixture(scope="module", autouse=True)
def seeded_world():
    """The real seeded fleet, because the acceptance criteria are about it.

    The deliberate responder placement — Echo nearest but wrong-skilled, Hotel at
    capacity, Golf offline — is the fixture these tests exist to exercise.
    """
    seed_module.run(reset=True, echo=lambda _line: None)


@pytest.fixture(autouse=True)
def isolated_fleet(seeded_world):
    """Restore fleet state around each test.

    Capacity is global and outlives whichever test consumed it, so without this the
    first few tests exhaust the fleet and everything after them defers.
    """
    _restore()
    yield
    _restore()


def _restore() -> None:
    """Return responders to their seeded state — not to a blank one.

    Resetting everything to `available` would quietly delete the at-capacity and
    offline fixtures that half of these tests depend on.
    """
    by_name = {spec.name: spec for spec in RESPONDER_SPECS}

    with Session(engine) as session:
        for assignment in session.exec(select(Assignment)).all():
            session.delete(assignment)

        for responder in session.exec(select(Responder)).all():
            spec = by_name.get(responder.name)
            if spec is not None:
                responder.active_count = spec.active_count
                responder.status = spec.status
                session.add(responder)

        # Return anything mid-dispatch to the queue.
        for report in session.exec(
            select(Report).where(Report.status == ReportStatus.ASSIGNED)
        ).all():
            report.status = ReportStatus.QUEUED
            session.add(report)

        session.commit()


def queued_report_id(client: TestClient) -> str:
    return client.get("/api/queue", params={"limit": 1}).json()["items"][0]["id"]


def events_for(report_id: str, activity: Activity) -> list[ProcessEvent]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(ProcessEvent).where(
                    ProcessEvent.case_id == uuid.UUID(report_id),
                    ProcessEvent.activity == activity.value,
                )
            ).all()
        )


# --- The happy path -----------------------------------------------------------------


def test_assigning_takes_the_top_of_the_queue(client: TestClient) -> None:
    expected = queued_report_id(client)

    response = client.post(ASSIGN, json={"operator": "controller-meera"})

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "assigned"
    assert body["report"]["id"] == expected
    assert body["report"]["status"] == ReportStatus.ASSIGNED.value
    assert body["responder"]["name"]
    assert body["assignment_id"]


def test_the_response_explains_the_match(client: TestClient) -> None:
    body = client.post(ASSIGN, json={}).json()

    match = body["match"]
    assert set(match) == {
        "distance_km",
        "distance_component",
        "skill_component",
        "load_component",
        "required_skill",
        "score",
    }
    assert 0 < match["score"] <= 1
    assert body["candidates_considered"] >= 1


def test_an_assigned_report_leaves_the_queue(client: TestClient) -> None:
    before = client.get("/api/queue", params={"limit": 1}).json()["total"]
    assigned = client.post(ASSIGN, json={}).json()["report"]["id"]

    after = client.get("/api/queue", params={"limit": 200}).json()
    assert after["total"] == before - 1
    assert assigned not in {item["id"] for item in after["items"]}


def test_assignment_consumes_responder_capacity(client: TestClient) -> None:
    body = client.post(ASSIGN, json={}).json()

    with Session(engine) as session:
        responder = session.get(Responder, uuid.UUID(body["responder"]["id"]))

    assert responder.active_count == body["responder"]["active_count"]
    assert responder.active_count >= 1
    assert responder.active_count <= responder.capacity


def test_a_specific_report_can_be_assigned(client: TestClient) -> None:
    queue = client.get("/api/queue", params={"limit": 5}).json()["items"]
    target = queue[2]["id"]  # deliberately not the head

    body = client.post(ASSIGN, json={"report_id": target}).json()

    assert body["outcome"] == "assigned"
    assert body["report"]["id"] == target


def count_all(activity: Activity) -> int:
    with Session(engine) as session:
        return len(
            session.exec(select(ProcessEvent).where(ProcessEvent.activity == activity.value)).all()
        )


def test_assignment_emits_an_event(client: TestClient) -> None:
    """Exactly one new event per assignment.

    Counted as a delta rather than an absolute, because the log is append-only: a
    report this module dispatched in an earlier test keeps those events even after its
    assignment is rolled back, and that is the correct behaviour.
    """
    before = count_all(Activity.ASSIGNED)

    body = client.post(ASSIGN, json={"operator": "controller-arun"}).json()

    assert count_all(Activity.ASSIGNED) == before + 1

    latest = max(events_for(body["report"]["id"], Activity.ASSIGNED), key=lambda e: e.timestamp)
    assert latest.resource == "operator:controller-arun"
    assert latest.event_metadata["responder_id"] == body["responder"]["id"]


# --- Capacity and the deferred path ------------------------------------------------------


def test_the_fleet_runs_out_before_the_queue_does(client: TestClient) -> None:
    """FR-20 end to end: capacity is never exceeded, and the surplus stays queued."""
    with Session(engine) as session:
        responders = session.exec(select(Responder)).all()
        available_slots = sum(
            r.capacity - r.active_count
            for r in responders
            if r.status == ResponderStatus.AVAILABLE
        )
        queue_depth = len(build_queue(session))

    assert queue_depth > available_slots, "seed should have more reports than crew slots"

    outcomes = [
        client.post(ASSIGN, json={}).json()["outcome"] for _ in range(available_slots + 1)
    ]

    assert outcomes[:available_slots] == ["assigned"] * available_slots
    assert outcomes[-1] == "deferred"

    with Session(engine) as session:
        for responder in session.exec(select(Responder)).all():
            assert responder.active_count <= responder.capacity


def test_a_deferred_report_stays_in_the_queue(client: TestClient) -> None:
    with Session(engine) as session:
        for responder in session.exec(select(Responder)).all():
            responder.status = ResponderStatus.OFFLINE
            session.add(responder)
        session.commit()

    before = client.get("/api/queue", params={"limit": 1}).json()
    target = before["items"][0]["id"]

    body = client.post(ASSIGN, json={}).json()

    assert body["outcome"] == "deferred"
    assert body["reason"]
    after = client.get("/api/queue", params={"limit": 1}).json()
    assert after["total"] == before["total"]
    assert after["items"][0]["id"] == target
    assert events_for(target, Activity.DISPATCH_DEFERRED)


def test_deferral_is_not_an_http_error(client: TestClient) -> None:
    """Nobody being free is a state of the world, not a fault in the request."""
    with Session(engine) as session:
        for responder in session.exec(select(Responder)).all():
            responder.status = ResponderStatus.OFFLINE
            session.add(responder)
        session.commit()

    assert client.post(ASSIGN, json={}).status_code == 200


# --- Guard rails ---------------------------------------------------------------------------


def test_assigning_an_unknown_report_is_a_typed_error(client: TestClient) -> None:
    response = client.post(
        ASSIGN, json={"report_id": "00000000-0000-0000-0000-000000000000"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPORT_NOT_FOUND"


def test_a_report_outside_the_queue_cannot_be_assigned(client: TestClient) -> None:
    first = client.post(ASSIGN, json={}).json()["report"]["id"]

    again = client.post(ASSIGN, json={"report_id": first})

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "REPORT_NOT_IN_QUEUE"


def test_a_report_can_only_be_assigned_once(client: TestClient) -> None:
    report_id = client.post(ASSIGN, json={}).json()["report"]["id"]

    with Session(engine) as session:
        open_assignments = [
            a
            for a in session.exec(
                select(Assignment).where(Assignment.report_id == uuid.UUID(report_id))
            ).all()
            if a.is_open
        ]

    assert len(open_assignments) == 1


def test_dispatch_works_with_no_body(client: TestClient) -> None:
    assert client.post(ASSIGN).status_code == 200
