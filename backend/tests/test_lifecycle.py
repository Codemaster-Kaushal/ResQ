"""Phase 8: the responder lifecycle (FR-21, FR-22).

Acceptance: the full happy path to `resolved`; an illegal transition returns a typed
error; a rejected report retains its original ageing.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
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
from seed import seed as seed_module
from seed.fixtures.responders import RESPONDER_SPECS

ASSIGN = "/api/dispatch/assign"
HAPPY_PATH = ["acknowledged", "en_route", "on_scene", "resolved", "closed"]


@pytest.fixture(scope="module", autouse=True)
def seeded_world():
    seed_module.run(reset=True, echo=lambda _line: None)


@pytest.fixture(autouse=True)
def isolated_fleet(seeded_world):
    _restore()
    yield
    _restore()


def _restore() -> None:
    """Return the fleet and the queue to their seeded state between tests."""
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
        for report in session.exec(select(Report)).all():
            if report.status in {
                ReportStatus.ASSIGNED,
                ReportStatus.ACKNOWLEDGED,
                ReportStatus.EN_ROUTE,
                ReportStatus.ON_SCENE,
                ReportStatus.RESOLVED,
                ReportStatus.CLOSED,
            }:
                report.status = ReportStatus.QUEUED
                session.add(report)
        session.commit()


@pytest.fixture
def assignment(client: TestClient) -> dict:
    """One freshly dispatched assignment."""
    body = client.post(ASSIGN, json={"operator": "controller-meera"}).json()
    assert body["outcome"] == "assigned", body
    return body


def status_url(assignment_id: str) -> str:
    return f"/api/assignments/{assignment_id}/status"


def advance(client: TestClient, assignment_id: str, status: str, **kw):
    return client.post(status_url(assignment_id), json={"status": status, **kw})


def activities_for(report_id: str) -> list[str]:
    with Session(engine) as session:
        events = session.exec(
            select(ProcessEvent)
            .where(ProcessEvent.case_id == uuid.UUID(report_id))
            .order_by(ProcessEvent.timestamp, ProcessEvent.id)
        ).all()
    return [event.activity for event in events]


# --- Acceptance: the full happy path ------------------------------------------------


def test_the_full_lifecycle_runs_to_closed(client: TestClient, assignment: dict) -> None:
    assignment_id = assignment["assignment_id"]

    for status in HAPPY_PATH:
        response = advance(client, assignment_id, status, actor="responder-alpha")
        assert response.status_code == 200, response.json()
        assert response.json()["report_status"] == status

    detail = client.get(f"/api/reports/{assignment['report']['id']}").json()
    assert detail["status"] == ReportStatus.CLOSED.value


def test_the_lifecycle_leaves_a_complete_event_trail(client: TestClient, assignment: dict) -> None:
    """Phase 9 mines cycle times from this, so the trail has to be ordered and whole."""
    for status in HAPPY_PATH:
        advance(client, assignment["assignment_id"], status)

    trail = activities_for(assignment["report"]["id"])

    assert trail[-5:] == ["ACKNOWLEDGED", "EN_ROUTE", "ON_SCENE", "RESOLVED", "CLOSED"]


def test_acknowledgement_is_timestamped(client: TestClient, assignment: dict) -> None:
    body = advance(client, assignment["assignment_id"], "acknowledged").json()

    assert body["assignment"]["acknowledged_at"] is not None
    assert body["assignment"]["is_open"] is True


def test_resolving_frees_the_responder(client: TestClient, assignment: dict) -> None:
    """Capacity comes back when the crew finishes on scene, not after the paperwork."""
    responder_id = assignment["responder"]["id"]
    with Session(engine) as session:
        before = session.get(Responder, uuid.UUID(responder_id)).active_count

    for status in ["acknowledged", "en_route", "on_scene", "resolved"]:
        body = advance(client, assignment["assignment_id"], status).json()

    assert body["responder_active_count"] == before - 1
    assert body["assignment"]["resolved_at"] is not None
    assert body["assignment"]["is_open"] is False


def test_a_full_unit_becomes_available_again_once_resolved(client: TestClient) -> None:
    with Session(engine) as session:
        solo = session.exec(select(Responder).where(Responder.name == "Structural Crew Echo")).one()
        solo.capacity = 1
        solo.active_count = 0
        solo.status = ResponderStatus.AVAILABLE
        session.add(solo)
        for other in session.exec(select(Responder)).all():
            if other.id != solo.id:
                other.status = ResponderStatus.OFFLINE
                session.add(other)
        session.commit()

    body = client.post(ASSIGN, json={}).json()
    with Session(engine) as session:
        assert session.get(Responder, uuid.UUID(body["responder"]["id"])).status == (
            ResponderStatus.BUSY
        )

    for status in ["acknowledged", "en_route", "on_scene", "resolved"]:
        result = advance(client, body["assignment_id"], status).json()

    assert result["responder_status"] == ResponderStatus.AVAILABLE.value


def test_an_offline_unit_stays_offline_when_freed(client: TestClient, assignment: dict) -> None:
    """An operator took it out of service; finishing a job does not put it back."""
    responder_id = uuid.UUID(assignment["responder"]["id"])
    with Session(engine) as session:
        responder = session.get(Responder, responder_id)
        responder.status = ResponderStatus.OFFLINE
        session.add(responder)
        session.commit()

    for status in ["acknowledged", "en_route", "on_scene", "resolved"]:
        body = advance(client, assignment["assignment_id"], status).json()

    assert body["responder_status"] == ResponderStatus.OFFLINE.value


# --- Acceptance: illegal transitions -------------------------------------------------------


@pytest.mark.parametrize("target", ["on_scene", "resolved", "closed", "en_route"])
def test_skipping_a_step_is_refused(client: TestClient, assignment: dict, target: str) -> None:
    """A crew reporting 'on scene' without acknowledging has hit the wrong button, and
    accepting it would corrupt the cycle times Phase 9 mines."""
    response = advance(client, assignment["assignment_id"], target)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "ILLEGAL_TRANSITION"
    assert error["detail"]["current_status"] == "assigned"
    assert "acknowledged" in error["detail"]["allowed"]


def test_going_backwards_is_refused(client: TestClient, assignment: dict) -> None:
    advance(client, assignment["assignment_id"], "acknowledged")

    response = advance(client, assignment["assignment_id"], "assigned")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ILLEGAL_TRANSITION"


def test_a_resolved_assignment_can_still_be_closed(client: TestClient, assignment: dict) -> None:
    """Resolving frees the crew but does not end the case: `closed` is the control room
    signing off finished work, and it is still this assignment it belongs to."""
    for status in ["acknowledged", "en_route", "on_scene", "resolved"]:
        advance(client, assignment["assignment_id"], status)

    response = advance(client, assignment["assignment_id"], "closed", actor="controller-meera")

    assert response.status_code == 200
    assert response.json()["report_status"] == ReportStatus.CLOSED.value


def test_nothing_follows_closed(client: TestClient, assignment: dict) -> None:
    for status in HAPPY_PATH:
        advance(client, assignment["assignment_id"], status)

    response = advance(client, assignment["assignment_id"], "closed")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ILLEGAL_TRANSITION"


def test_a_rejected_assignment_cannot_be_updated(client: TestClient, assignment: dict) -> None:
    client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject", json={"reason": "Unavailable"}
    )

    response = advance(client, assignment["assignment_id"], "acknowledged")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSIGNMENT_CLOSED"


def test_queued_is_not_a_status_update(client: TestClient, assignment: dict) -> None:
    """Returning a report to the queue needs a reason, so it has its own endpoint."""
    response = advance(client, assignment["assignment_id"], "queued")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "ILLEGAL_TRANSITION"
    assert "reject" in error["detail"]["hint"]


def test_an_unknown_status_is_a_validation_error(client: TestClient, assignment: dict) -> None:
    response = advance(client, assignment["assignment_id"], "having_lunch")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_an_unknown_assignment_is_a_typed_error(client: TestClient) -> None:
    response = client.post(
        status_url("00000000-0000-0000-0000-000000000000"), json={"status": "acknowledged"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ASSIGNMENT_NOT_FOUND"


# --- Acceptance: rejection preserves ageing ---------------------------------------------------


def test_rejection_returns_the_report_to_the_queue(client: TestClient, assignment: dict) -> None:
    response = client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject",
        json={"reason": "Already committed to a larger incident", "actor": "responder-alpha"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["report_status"] == ReportStatus.QUEUED.value
    assert body["queue_position"] is not None
    assert body["assignment"]["rejection_reason"] == "Already committed to a larger incident"
    assert body["assignment"]["is_open"] is False


def test_a_rejected_report_keeps_its_original_ageing(client: TestClient, assignment: dict) -> None:
    """The acceptance criterion: a report bounced between crews must not be punished
    for their unavailability. Ageing runs from when the citizen filed it."""
    report_id = assignment["report"]["id"]
    before = client.get(f"/api/reports/{report_id}").json()["client_created_at"]

    client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject",
        json={"reason": "Vehicle broken down"},
    )

    after = client.get(f"/api/reports/{report_id}").json()
    assert after["client_created_at"] == before

    entry = next(
        item
        for item in client.get("/api/queue", params={"limit": 200}).json()["items"]
        if item["id"] == report_id
    )
    assert entry["priority"]["minutes_waiting"] > 0


def test_rejection_frees_the_responder(client: TestClient, assignment: dict) -> None:
    responder_id = uuid.UUID(assignment["responder"]["id"])
    with Session(engine) as session:
        before = session.get(Responder, responder_id).active_count

    body = client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject", json={"reason": "Unavailable"}
    ).json()

    assert body["responder_active_count"] == before - 1


def test_a_rejecting_responder_is_not_offered_the_same_report(
    client: TestClient, assignment: dict
) -> None:
    """Otherwise the report returns to the queue, matches them again, and loops."""
    report_id = assignment["report"]["id"]
    refuser = assignment["responder"]["id"]

    client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject", json={"reason": "Cannot attend"}
    )

    again = client.post(ASSIGN, json={"report_id": report_id}).json()

    assert again["outcome"] == "assigned"
    assert again["responder"]["id"] != refuser


def test_rejection_emits_an_event_with_the_reason(client: TestClient, assignment: dict) -> None:
    client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject",
        json={"reason": "Road is impassable", "actor": "responder-delta"},
    )

    with Session(engine) as session:
        # The log is append-only and every test in this module dispatches the same
        # top-of-queue report, so take the most recent rejection rather than the first.
        events = session.exec(
            select(ProcessEvent)
            .where(ProcessEvent.case_id == uuid.UUID(assignment["report"]["id"]))
            .where(ProcessEvent.activity == Activity.ASSIGNMENT_REJECTED.value)
            .order_by(ProcessEvent.timestamp, ProcessEvent.id)
        ).all()
        event = events[-1] if events else None

    assert event is not None
    assert event.resource == "responder-delta"
    assert event.event_metadata["reason"] == "Road is impassable"


def test_a_reason_is_required_to_reject(client: TestClient, assignment: dict) -> None:
    response = client.post(f"/api/assignments/{assignment['assignment_id']}/reject", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_a_resolved_assignment_cannot_be_rejected(client: TestClient, assignment: dict) -> None:
    for status in ["acknowledged", "en_route", "on_scene", "resolved"]:
        advance(client, assignment["assignment_id"], status)

    response = client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject", json={"reason": "Too late"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSIGNMENT_CLOSED"


def test_rejection_is_allowed_mid_lifecycle(client: TestClient, assignment: dict) -> None:
    """A crew can be recalled after acknowledging or while en route."""
    advance(client, assignment["assignment_id"], "acknowledged")
    advance(client, assignment["assignment_id"], "en_route")

    response = client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject",
        json={"reason": "Diverted to a larger incident"},
    )

    assert response.status_code == 200
    assert response.json()["report_status"] == ReportStatus.QUEUED.value


def test_a_report_cannot_be_rejected_once_on_scene(client: TestClient, assignment: dict) -> None:
    for status in ["acknowledged", "en_route", "on_scene"]:
        advance(client, assignment["assignment_id"], status)

    response = client.post(
        f"/api/assignments/{assignment['assignment_id']}/reject", json={"reason": "Changed mind"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ILLEGAL_TRANSITION"


# --- The roster ------------------------------------------------------------------------------------


def test_the_roster_reports_live_load(client: TestClient) -> None:
    body = client.get("/api/responders").json()

    assert body["total"] == len(RESPONDER_SPECS)
    for item in body["items"]:
        assert item["spare_capacity"] == max(0, item["capacity"] - item["active_count"])
        assert item["dispatchable"] == (
            item["status"] == "available" and item["active_count"] < item["capacity"]
        )


def test_the_roster_tracks_an_assignment(client: TestClient, assignment: dict) -> None:
    responder_id = assignment["responder"]["id"]

    item = next(
        r for r in client.get("/api/responders").json()["items"] if r["id"] == responder_id
    )

    assert item["open_assignments"] >= 1


def test_the_roster_filters(client: TestClient) -> None:
    offline = client.get("/api/responders", params={"status": "offline"}).json()
    medical = client.get("/api/responders", params={"skill": "medical"}).json()
    ready = client.get("/api/responders", params={"dispatchable": "true"}).json()

    assert {i["name"] for i in offline["items"]} == {"Rescue Team Golf"}
    assert all(i["skill"] == "medical" for i in medical["items"])
    assert all(i["dispatchable"] for i in ready["items"])
    assert "Medical Unit Hotel" not in {i["name"] for i in ready["items"]}  # at capacity


def test_the_roster_rejects_an_oversized_page(client: TestClient) -> None:
    response = client.get("/api/responders", params={"limit": 9999})

    assert response.status_code == 422
