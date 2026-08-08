"""Phase 6 acceptance, through the API.

A high-severity report filed *last* appears *first*; a low-severity report left waiting
climbs; an override pins and emits a process event.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db import engine
from app.models import Activity, ProcessEvent, Report, ReportStatus
from tests.test_ingestion import post_report

QUEUE = "/api/queue"

# Far from every other test report, so this module's queue arithmetic is not perturbed
# by corroboration from reports posted at the shared default coordinates.
ISLAND = {"lat": -20.5, "lng": 57.5}

_seq = 0


@pytest.fixture(autouse=True)
def isolated_overrides():
    """Clear every pin around each test.

    An override outlives the test that set it and sorts above all computed scores, so
    without this one test's pin silently owns position 1 for every test that follows.
    """
    _clear_all_overrides()
    yield
    _clear_all_overrides()


def _clear_all_overrides() -> None:
    with Session(engine) as session:
        pinned = session.exec(
            select(Report).where(Report.manual_override_rank.is_not(None))
        ).all()
        for report in pinned:
            report.manual_override_rank = None
            session.add(report)
        if pinned:
            session.commit()


def _key(label: str) -> str:
    global _seq
    _seq += 1
    return f"p6-{label}-{_seq}"


def file_report(client: TestClient, *, severity_text: str, hours_ago: float = 0, **kw) -> dict:
    filed = utcnow() - timedelta(hours=hours_ago)
    return post_report(
        client,
        text=severity_text,
        client_created_at=filed.isoformat(),
        **ISLAND,
        **kw,
    ).json()


CATASTROPHE = (
    "The building has collapsed completely, at least twenty people are trapped inside "
    "with no way out, children are crying under the debris and one man is bleeding "
    "badly and not breathing."
)
MINOR = "The street light outside our gate has been off for a week."


def queue_keys(client: TestClient) -> list[str]:
    return [item["idempotency_key"] for item in client.get(QUEUE, params={"limit": 200}).json()["items"]]


def entry_for(client: TestClient, key: str) -> dict | None:
    for item in client.get(QUEUE, params={"limit": 200}).json()["items"]:
        if item["idempotency_key"] == key:
            return item
    return None


# --- Acceptance: severity beats arrival order --------------------------------------------


def test_a_catastrophe_filed_last_appears_first(client: TestClient) -> None:
    """The headline claim: arrival order does not decide who gets help."""
    early = _key("early-minor")
    late = _key("late-critical")

    file_report(client, severity_text=MINOR, hours_ago=3, idempotency_key=early)
    file_report(client, severity_text=CATASTROPHE, hours_ago=0, idempotency_key=late)

    keys = queue_keys(client)

    assert keys.index(late) < keys.index(early)


def test_the_queue_explains_every_position(client: TestClient) -> None:
    key = _key("explained")
    file_report(client, severity_text=CATASTROPHE, idempotency_key=key)

    item = entry_for(client, key)

    assert item is not None
    assert set(item["priority"]) == {
        "severity",
        "authenticity",
        "ageing_bonus",
        "minutes_waiting",
        "score",
    }
    assert item["priority"]["score"] == item["priority_score"]
    assert item["position"] >= 1


def test_ageing_lifts_a_long_waiting_report(client: TestClient) -> None:
    """A minor report filed hours ago outranks the same report filed just now."""
    old_key, new_key = _key("waited"), _key("fresh")
    file_report(client, severity_text=MINOR, hours_ago=4, idempotency_key=old_key)
    file_report(client, severity_text=MINOR, hours_ago=0, idempotency_key=new_key)

    waited, fresh = entry_for(client, old_key), entry_for(client, new_key)

    assert waited["priority"]["ageing_bonus"] > fresh["priority"]["ageing_bonus"]
    assert waited["priority_score"] > fresh["priority_score"]
    assert queue_keys(client).index(old_key) < queue_keys(client).index(new_key)


def test_the_queue_holds_only_reports_awaiting_dispatch(client: TestClient) -> None:
    key = _key("membership")
    created = file_report(client, severity_text=CATASTROPHE, idempotency_key=key)

    assert entry_for(client, key) is not None

    with Session(engine) as session:
        report = session.get(Report, uuid.UUID(created["id"]))
        report.status = ReportStatus.ASSIGNED
        session.add(report)
        session.commit()

    assert entry_for(client, key) is None


def test_reading_the_queue_persists_the_refreshed_score(client: TestClient) -> None:
    """TRD §3: priority_score is "computed, refreshed on read"."""
    key = _key("persisted")
    created = file_report(client, severity_text=CATASTROPHE, idempotency_key=key)

    item = entry_for(client, key)

    with Session(engine) as session:
        stored = session.get(Report, uuid.UUID(created["id"])).priority_score

    assert stored == pytest.approx(item["priority_score"])


def test_the_queue_paginates(client: TestClient) -> None:
    first = client.get(QUEUE, params={"limit": 2, "offset": 0}).json()
    second = client.get(QUEUE, params={"limit": 2, "offset": 2}).json()

    assert first["total"] == second["total"]
    assert len(first["items"]) <= 2
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in second["items"]})
    assert [i["position"] for i in second["items"]][:1] in ([3], [])


def test_an_oversized_page_is_rejected(client: TestClient) -> None:
    response = client.get(QUEUE, params={"limit": 10_000})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- Acceptance: override pins and emits an event ---------------------------------------------


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


def test_pinning_lifts_a_report_to_the_top(client: TestClient) -> None:
    minor_key = _key("to-pin")
    file_report(client, severity_text=CATASTROPHE, idempotency_key=_key("big"))
    minor = file_report(client, severity_text=MINOR, idempotency_key=minor_key)

    response = client.post(
        f"{QUEUE}/{minor['id']}/override",
        json={"action": "pin", "operator": "controller-meera", "reason": "Caller is a nurse"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["position"] == 1
    assert body["previous_position"] > 1
    assert queue_keys(client)[0] == minor_key
    assert entry_for(client, minor_key)["pinned"] is True


def test_an_override_emits_a_process_event(client: TestClient) -> None:
    """FR-18/FR-30: the human-in-the-loop claim has to be evidenced in the log."""
    created = file_report(client, severity_text=MINOR, idempotency_key=_key("event"))

    response = client.post(
        f"{QUEUE}/{created['id']}/override",
        json={"action": "pin", "operator": "controller-meera", "reason": "Known hazard"},
    )

    events = events_for(created["id"], Activity.PRIORITY_OVERRIDDEN)

    assert len(events) == 1
    event = events[0]
    assert event.resource == "operator:controller-meera"
    assert event.event_metadata["action"] == "pin"
    assert event.event_metadata["reason"] == "Known hazard"
    assert event.event_metadata["previous_rank"] is None
    assert str(event.id) == response.json()["event_id"]


def test_demoting_pushes_a_report_to_the_bottom(client: TestClient) -> None:
    key = _key("to-demote")
    created = file_report(client, severity_text=CATASTROPHE, idempotency_key=key)

    client.post(
        f"{QUEUE}/{created['id']}/override",
        json={"action": "demote", "operator": "controller-meera", "reason": "Confirmed hoax risk"},
    )

    assert queue_keys(client)[-1] == key
    assert entry_for(client, key)["demoted"] is True


def test_clearing_restores_the_computed_position(client: TestClient) -> None:
    key = _key("to-clear")
    created = file_report(client, severity_text=MINOR, idempotency_key=key)

    client.post(
        f"{QUEUE}/{created['id']}/override", json={"action": "pin", "operator": "controller-meera"}
    )
    assert queue_keys(client)[0] == key

    cleared = client.post(
        f"{QUEUE}/{created['id']}/override", json={"action": "clear", "operator": "controller-meera"}
    )

    assert cleared.json()["manual_override_rank"] is None
    assert entry_for(client, key)["pinned"] is False
    assert queue_keys(client)[0] != key


def test_every_override_is_logged_including_the_undo(client: TestClient) -> None:
    created = file_report(client, severity_text=MINOR, idempotency_key=_key("audit"))

    for action in ("pin", "demote", "clear"):
        client.post(
            f"{QUEUE}/{created['id']}/override",
            json={"action": action, "operator": "controller-arun"},
        )

    events = events_for(created["id"], Activity.PRIORITY_OVERRIDDEN)

    assert [event.event_metadata["action"] for event in events] == ["pin", "demote", "clear"]


def test_an_explicit_rank_positions_within_the_pinned_band(client: TestClient) -> None:
    first_key, second_key = _key("rank-a"), _key("rank-b")
    first = file_report(client, severity_text=MINOR, idempotency_key=first_key)
    second = file_report(client, severity_text=MINOR, idempotency_key=second_key)

    client.post(
        f"{QUEUE}/{second['id']}/override",
        json={"action": "pin", "operator": "op", "rank": 1},
    )
    client.post(
        f"{QUEUE}/{first['id']}/override",
        json={"action": "pin", "operator": "op", "rank": 0},
    )

    keys = queue_keys(client)
    assert keys[:2] == [first_key, second_key]


# --- Guard rails -------------------------------------------------------------------------------


def test_overriding_an_unknown_report_is_a_typed_error(client: TestClient) -> None:
    response = client.post(
        f"{QUEUE}/00000000-0000-0000-0000-000000000000/override",
        json={"action": "pin", "operator": "op"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPORT_NOT_FOUND"


def test_a_report_outside_the_queue_cannot_be_reordered(client: TestClient) -> None:
    created = file_report(client, severity_text=CATASTROPHE, idempotency_key=_key("not-queued"))
    with Session(engine) as session:
        report = session.get(Report, uuid.UUID(created["id"]))
        report.status = ReportStatus.FLAGGED
        session.add(report)
        session.commit()

    response = client.post(
        f"{QUEUE}/{created['id']}/override", json={"action": "pin", "operator": "op"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REPORT_NOT_IN_QUEUE"


def test_an_operator_identity_is_required(client: TestClient) -> None:
    created = file_report(client, severity_text=MINOR, idempotency_key=_key("anon"))

    response = client.post(f"{QUEUE}/{created['id']}/override", json={"action": "pin"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_an_unknown_action_is_rejected(client: TestClient) -> None:
    created = file_report(client, severity_text=MINOR, idempotency_key=_key("bad-action"))

    response = client.post(
        f"{QUEUE}/{created['id']}/override", json={"action": "delete", "operator": "op"}
    )

    assert response.status_code == 422


def test_a_negative_rank_is_rejected(client: TestClient) -> None:
    """Direction comes from the action, not from the sign the caller supplies."""
    created = file_report(client, severity_text=MINOR, idempotency_key=_key("neg-rank"))

    response = client.post(
        f"{QUEUE}/{created['id']}/override",
        json={"action": "pin", "operator": "op", "rank": -5},
    )

    assert response.status_code == 422
