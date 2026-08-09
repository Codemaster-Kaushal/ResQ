"""Phase 10: offline batch sync (FR-26 … FR-28).

Acceptance: the same batch posted twice creates no duplicates, and offline-filed
reports keep their original wait time.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, func, select

from app.config import settings
from app.core.time import utcnow
from app.db import engine
from app.models import Activity, ProcessEvent, Report, ReportStatus

SYNC = "/api/sync/reports"

# Well away from the other tests' coordinates, so corroboration does not perturb scores.
FAR_LAT, FAR_LNG = -33.9, 18.4

_seq = 0


def _key(label: str) -> str:
    global _seq
    _seq += 1
    return f"p10-{label}-{_seq}"


def item(key: str, *, hours_ago: float = 2.0, text: str | None = None, **overrides) -> dict:
    payload = {
        "idempotency_key": key,
        "text": text or "Water is rising in the basement and a watchman is trapped inside",
        "lat": FAR_LAT,
        "lng": FAR_LNG,
        "client_created_at": (utcnow() - timedelta(hours=hours_ago)).isoformat(),
        "reporter_pseudonym": "offline-heron-01",
    }
    payload.update(overrides)
    return payload


def count_reports(prefix: str) -> int:
    with Session(engine) as session:
        return session.exec(
            select(func.count())
            .select_from(Report)
            .where(Report.idempotency_key.like(f"{prefix}%"))  # type: ignore[union-attr]
        ).one()


# --- Acceptance: the same batch twice is a no-op ----------------------------------------


def test_a_batch_syncs(client: TestClient) -> None:
    keys = [_key("batch") for _ in range(3)]

    response = client.post(SYNC, json={"reports": [item(k) for k in keys], "device_id": "phone-7"})

    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 3
    assert body["created"] == 3
    assert body["duplicates"] == 0
    assert body["rejected"] == 0
    assert {r["idempotency_key"] for r in body["results"]} == set(keys)


def test_resending_the_same_batch_creates_nothing(client: TestClient) -> None:
    """The acceptance criterion: a retried sync must be safe."""
    batch = {"reports": [item(_key("repeat")) for _ in range(4)]}

    first = client.post(SYNC, json=batch).json()
    second = client.post(SYNC, json=batch).json()

    assert first["created"] == 4
    assert second["created"] == 0
    assert second["duplicates"] == 4
    assert all(r["outcome"] == "duplicate" for r in second["results"])

    # And the same rows, not new ones.
    assert {r["report_id"] for r in first["results"]} == {
        r["report_id"] for r in second["results"]
    }


def test_a_partially_seen_batch_only_creates_the_new_reports(client: TestClient) -> None:
    """A sync that timed out halfway is simply retried with the whole queue."""
    old = [item(_key("partial-old")) for _ in range(2)]
    client.post(SYNC, json={"reports": old})

    new = [item(_key("partial-new")) for _ in range(3)]
    body = client.post(SYNC, json={"reports": old + new}).json()

    assert body["received"] == 5
    assert body["created"] == 3
    assert body["duplicates"] == 2


def test_a_key_repeated_inside_one_batch_is_deduplicated(client: TestClient) -> None:
    """A device that double-queued the same report should not create two."""
    key = _key("double")

    body = client.post(SYNC, json={"reports": [item(key), item(key)]}).json()

    assert body["created"] == 1
    assert body["duplicates"] == 1
    assert count_reports(key) == 1


def test_sync_and_the_single_endpoint_share_one_keyspace(client: TestClient) -> None:
    """A report filed online must not be re-created by a later offline sync."""
    key = _key("shared")
    client.post(
        "/api/reports",
        data={"text": "Filed while online", "lat": FAR_LAT, "lng": FAR_LNG, "idempotency_key": key},
    )

    body = client.post(SYNC, json={"reports": [item(key)]}).json()

    assert body["duplicates"] == 1
    assert count_reports(key) == 1


# --- Acceptance: the wait time survives the sync ------------------------------------------


def test_an_offline_report_keeps_its_original_wait(client: TestClient) -> None:
    """FR-28: filed three hours ago means waiting three hours, however late it syncs."""
    key = _key("waited")
    filed = utcnow() - timedelta(hours=3)

    result = client.post(
        SYNC, json={"reports": [item(key, client_created_at=filed.isoformat())]}
    ).json()["results"][0]

    assert result["minutes_waiting"] == pytest.approx(180, abs=2)
    assert result["client_timestamp_supplied"] is True

    detail = client.get(f"/api/reports/{result['report_id']}").json()
    assert detail["client_created_at"] < detail["received_at"]


def test_the_queue_ages_a_synced_report_from_when_it_was_filed(client: TestClient) -> None:
    """The wait is not just recorded — it counts. A late sync does not lose its place."""
    late = _key("late-sync")
    fresh = _key("fresh-sync")

    client.post(
        SYNC,
        json={
            "reports": [
                item(late, hours_ago=4, text="Street light out for a week in our lane"),
                item(fresh, hours_ago=0, text="Street light out for a week in our lane"),
            ]
        },
    )

    entries = {
        e["idempotency_key"]: e
        for e in client.get("/api/queue", params={"limit": 200}).json()["items"]
    }

    assert entries[late]["priority"]["ageing_bonus"] > entries[fresh]["priority"]["ageing_bonus"]
    assert entries[late]["priority_score"] > entries[fresh]["priority_score"]


def test_a_missing_client_timestamp_is_flagged_not_rejected(client: TestClient) -> None:
    key = _key("no-clock")

    result = client.post(
        SYNC, json={"reports": [item(key, client_created_at=None)]}
    ).json()["results"][0]

    assert result["outcome"] == "created"
    assert result["client_timestamp_supplied"] is False


def test_a_timezone_offset_is_normalised(client: TestClient) -> None:
    key = _key("tz")

    result = client.post(
        SYNC, json={"reports": [item(key, client_created_at="2026-08-09T15:00:00+05:30")]}
    ).json()["results"][0]

    assert result["client_created_at"].startswith("2026-08-09T09:30")


# --- Partial success ------------------------------------------------------------------------


def test_one_bad_report_does_not_cost_the_others(client: TestClient) -> None:
    """Losing nineteen good reports over one corrupt GPS fix is the opposite of the point."""
    good = [item(_key("good")) for _ in range(3)]
    bad = item(_key("bad"), lat=91.0)

    body = client.post(SYNC, json={"reports": good + [bad]}).json()

    assert body["created"] == 3
    assert body["rejected"] == 1
    rejected = next(r for r in body["results"] if r["outcome"] == "rejected")
    assert rejected["error_code"] == "INVALID_COORDINATES"
    assert rejected["report_id"] is None


def test_a_rejected_item_is_not_persisted(client: TestClient) -> None:
    key = _key("not-stored")

    client.post(SYNC, json={"reports": [item(key, lng=200.0)]})

    assert count_reports(key) == 0


def test_an_oversized_batch_is_refused_with_a_typed_error(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sync_max_batch_size", 2)

    response = client.post(SYNC, json={"reports": [item(_key("big")) for _ in range(3)]})

    assert response.status_code == 413
    detail = response.json()["error"]["detail"]
    assert detail["limit"] == 2
    assert "idempotent" in detail["hint"]


def test_an_empty_batch_is_a_validation_error(client: TestClient) -> None:
    response = client.post(SYNC, json={"reports": []})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- Synced reports join the normal pipeline ---------------------------------------------------


def test_synced_reports_are_scored_like_any_other(client: TestClient) -> None:
    key = _key("scored")

    result = client.post(SYNC, json={"reports": [item(key)]}).json()["results"][0]
    detail = client.get(f"/api/reports/{result['report_id']}").json()

    assert detail["severity_score"] is not None
    assert detail["severity_reasons"]
    assert detail["authenticity_score"] is not None
    assert detail["status"] in {ReportStatus.QUEUED.value, ReportStatus.FLAGGED.value}


def test_sync_returns_before_scoring(client: TestClient) -> None:
    """Same guarantee as single ingestion: the response does not wait on the scorer."""
    result = client.post(SYNC, json={"reports": [item(_key("async"))]}).json()["results"][0]

    assert result["status"] == ReportStatus.RECEIVED.value


def test_the_offline_channel_is_recorded_on_the_event(client: TestClient) -> None:
    key = _key("channel")

    result = client.post(
        SYNC, json={"reports": [item(key)], "device_id": "field-tablet-3"}
    ).json()["results"][0]

    with Session(engine) as session:
        event = session.exec(
            select(ProcessEvent)
            .where(ProcessEvent.case_id == uuid.UUID(result["report_id"]))
            .where(ProcessEvent.activity == Activity.REPORT_RECEIVED.value)
        ).one()

    assert event.event_metadata["channel"] == "offline_sync"
    assert event.event_metadata["device_id"] == "field-tablet-3"
    assert event.event_metadata["offline_delay_minutes"] > 0
