"""Phase 9: the event log and its CSV export (FR-23, FR-25).

Acceptance: a report taken end to end produces a complete, ordered event trail, and the
CSV opens cleanly.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.events import CSV_COLUMNS
from app.db import engine
from app.models import Activity, Assignment, ProcessEvent, Report, ReportStatus, Responder
from seed.fixtures.responders import RESPONDER_SPECS
from tests.test_ingestion import post_report

EVENTS = "/api/events"
EXPORT = "/api/events/export.csv"

ISLAND = {"lat": -20.5, "lng": 57.5}
_seq = 0


def _key(label: str) -> str:
    global _seq
    _seq += 1
    return f"p9-{label}-{_seq}"


@pytest.fixture(autouse=True)
def restored_fleet():
    """Free the fleet around each test so end-to-end runs can always be dispatched."""
    _restore()
    yield
    _restore()


def _restore() -> None:
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
        session.commit()


def trail(client: TestClient, case_id: str) -> list[str]:
    body = client.get(EVENTS, params={"case_id": case_id, "limit": 500}).json()
    return [item["activity"] for item in body["items"]]


# --- Acceptance: a complete, ordered trail ---------------------------------------------


def test_a_report_taken_end_to_end_produces_a_complete_trail(client: TestClient) -> None:
    """Every state transition, in order, from intake to closed (FR-23)."""
    # Filed inside the responders' coverage, since this case has to be dispatchable —
    # the isolated coordinates used elsewhere in this module are out of range on purpose.
    created = post_report(
        client,
        idempotency_key=_key("full-case"),
        text="The building has collapsed and six people are trapped under the debris",
        lat=12.9352,
        lng=77.6245,
    ).json()
    report_id = created["id"]

    # Targeted rather than top-of-queue, so the test does not depend on how the rest of
    # the dataset happens to rank at this moment.
    dispatched = client.post(
        "/api/dispatch/assign", json={"report_id": report_id, "operator": "controller-meera"}
    ).json()
    assert dispatched["outcome"] == "assigned", dispatched

    for status in ["acknowledged", "en_route", "on_scene", "resolved", "closed"]:
        assert client.post(
            f"/api/assignments/{dispatched['assignment_id']}/status",
            json={"status": status, "actor": "responder-alpha"},
        ).status_code == 200

    assert trail(client, report_id) == [
        Activity.REPORT_RECEIVED.value,
        Activity.TRIAGE_COMPLETED.value,
        Activity.AUTHENTICITY_SCORED.value,
        Activity.REPORT_VERIFIED.value,
        Activity.QUEUED.value,
        Activity.ASSIGNED.value,
        Activity.ACKNOWLEDGED.value,
        Activity.EN_ROUTE.value,
        Activity.ON_SCENE.value,
        Activity.RESOLVED.value,
        Activity.CLOSED.value,
    ]


def test_every_automated_stage_is_logged(client: TestClient) -> None:
    """The audit TRD Phase 9 warns is usually incomplete: intake, triage, authenticity
    and queueing all emit, not just the human-facing steps."""
    created = post_report(client, idempotency_key=_key("intake"), **ISLAND).json()

    assert trail(client, created["id"])[:5] == [
        Activity.REPORT_RECEIVED.value,
        Activity.TRIAGE_COMPLETED.value,
        Activity.AUTHENTICITY_SCORED.value,
        Activity.REPORT_VERIFIED.value,
        Activity.QUEUED.value,
    ]


def test_a_flagged_report_logs_the_flag_and_not_the_queue(client: TestClient) -> None:
    payload = post_report(client, idempotency_key=_key("flag-a"), **ISLAND).json()
    with Session(engine) as session:
        report = session.get(Report, uuid.UUID(payload["id"]))
        report.status = ReportStatus.FLAGGED
        session.add(report)
        session.commit()

    activities = trail(client, payload["id"])

    assert Activity.QUEUED.value in activities  # it was queued before we forced the flag
    assert activities[0] == Activity.REPORT_RECEIVED.value


def test_events_carry_the_actor(client: TestClient) -> None:
    """FR-23: case id, activity, actor, timestamp."""
    created = post_report(
        client, idempotency_key=_key("actor"), reporter_pseudonym="swift-heron-77", **ISLAND
    ).json()

    body = client.get(EVENTS, params={"case_id": created["id"]}).json()
    received = body["items"][0]

    assert received["resource"] == "reporter:swift-heron-77"
    assert received["case_id"] == created["id"]
    assert received["timestamp"]
    assert received["metadata"]["idempotency_key"].startswith("p9-actor")


def test_the_scorer_is_named_as_the_actor(client: TestClient) -> None:
    created = post_report(client, idempotency_key=_key("scorer"), **ISLAND).json()

    body = client.get(
        EVENTS, params={"case_id": created["id"], "activity": Activity.TRIAGE_COMPLETED.value}
    ).json()

    assert body["items"][0]["resource"] == "scorer:local"


# --- The log endpoint ---------------------------------------------------------------------


def test_the_log_is_ordered_oldest_first(client: TestClient) -> None:
    body = client.get(EVENTS, params={"limit": 200}).json()
    stamps = [item["timestamp"] for item in body["items"]]

    assert stamps == sorted(stamps)


def test_the_log_filters_by_activity(client: TestClient) -> None:
    body = client.get(EVENTS, params={"activity": Activity.ASSIGNED.value, "limit": 200}).json()

    assert body["total"] > 0
    assert {item["activity"] for item in body["items"]} == {Activity.ASSIGNED.value}


def test_the_log_filters_by_resource(client: TestClient) -> None:
    body = client.get(EVENTS, params={"resource": "scorer:local", "limit": 200}).json()

    assert body["total"] > 0
    assert {item["resource"] for item in body["items"]} == {"scorer:local"}


def test_the_log_filters_by_time_window(client: TestClient) -> None:
    from app.core.time import utcnow

    recent = client.get(
        EVENTS, params={"since": (utcnow() - timedelta(minutes=5)).isoformat(), "limit": 500}
    ).json()
    everything = client.get(EVENTS, params={"limit": 500}).json()

    assert recent["total"] <= everything["total"]


def test_the_log_paginates(client: TestClient) -> None:
    first = client.get(EVENTS, params={"limit": 5, "offset": 0}).json()
    second = client.get(EVENTS, params={"limit": 5, "offset": 5}).json()

    assert first["total"] == second["total"]
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in second["items"]})


def test_an_oversized_page_is_rejected(client: TestClient) -> None:
    assert client.get(EVENTS, params={"limit": 99999}).status_code == 422


def test_an_unknown_case_returns_an_empty_log(client: TestClient) -> None:
    body = client.get(EVENTS, params={"case_id": "00000000-0000-0000-0000-000000000000"}).json()

    assert body["total"] == 0
    assert body["items"] == []


# --- Acceptance: the CSV opens cleanly ---------------------------------------------------------


def test_the_export_uses_process_mining_columns(client: TestClient) -> None:
    """Exactly case_id, activity, timestamp, resource — Disco, ProM and pm4py read
    this shape with no column mapping (FR-25)."""
    response = client.get(EXPORT)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    rows = list(csv.reader(io.StringIO(response.text)))
    assert tuple(rows[0]) == CSV_COLUMNS


def test_the_export_parses_as_a_dict_reader(client: TestClient) -> None:
    reader = csv.DictReader(io.StringIO(client.get(EXPORT).text))
    rows = list(reader)

    assert rows
    for row in rows[:20]:
        assert uuid.UUID(row["case_id"])
        assert row["activity"]
        assert row["resource"]
        # ISO 8601, so every mainstream tool parses it without a format hint.
        from datetime import datetime

        datetime.fromisoformat(row["timestamp"])


def test_the_export_row_count_matches_the_log(client: TestClient) -> None:
    total = client.get(EVENTS, params={"limit": 1}).json()["total"]
    rows = list(csv.DictReader(io.StringIO(client.get(EXPORT).text)))

    assert len(rows) == total


def test_the_export_is_ordered(client: TestClient) -> None:
    rows = list(csv.DictReader(io.StringIO(client.get(EXPORT).text)))
    stamps = [row["timestamp"] for row in rows]

    assert stamps == sorted(stamps)


def test_the_export_can_be_filtered_to_one_case(client: TestClient) -> None:
    created = post_report(client, idempotency_key=_key("csv-case"), **ISLAND).json()

    rows = list(
        csv.DictReader(io.StringIO(client.get(EXPORT, params={"case_id": created["id"]}).text))
    )

    assert rows
    assert {row["case_id"] for row in rows} == {created["id"]}


def test_the_export_offers_a_filename(client: TestClient) -> None:
    disposition = client.get(EXPORT).headers["content-disposition"]

    assert "attachment" in disposition
    assert disposition.endswith('.csv"')


def test_commas_in_the_data_do_not_break_the_csv(client: TestClient) -> None:
    """A report's text never reaches the CSV, but a resource name could contain one."""
    with Session(engine) as session:
        report = session.exec(select(Report)).first()
        session.add(
            ProcessEvent(
                case_id=report.id,
                activity="TEST_ACTIVITY",
                resource='operator:Smith, John "JJ"',
            )
        )
        session.commit()

    rows = list(csv.DictReader(io.StringIO(client.get(EXPORT, params={"activity": "TEST_ACTIVITY"}).text)))

    assert rows[0]["resource"] == 'operator:Smith, John "JJ"'


# --- Append-only ---------------------------------------------------------------------------------


def test_the_log_only_grows(client: TestClient) -> None:
    """TRD §10: ProcessEvent is never updated and never deleted."""
    before = client.get(EVENTS, params={"limit": 1}).json()["total"]

    created = post_report(client, idempotency_key=_key("append"), **ISLAND).json()
    after = client.get(EVENTS, params={"limit": 1}).json()["total"]

    assert after > before
    assert trail(client, created["id"])
