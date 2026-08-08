"""Phase 5: human review of flagged reports.

`POST /api/reports/{id}/review` is the only route to `rejected`. Nothing automated may
take that decision (FR-15), and every decision records who made it (FR-30).
"""

from __future__ import annotations

import uuid
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.models import Report, ReportStatus
from tests.test_ingestion import jpeg_bytes, post_report

ENDPOINT = "/api/reports"


def set_status(report_id: str, status: ReportStatus) -> None:
    """Put a report into a given state directly, so the test targets review alone."""
    with Session(engine) as session:
        report = session.get(Report, uuid.UUID(report_id))
        assert report is not None
        report.status = status
        session.add(report)
        session.commit()


@pytest.fixture
def flagged_report(client: TestClient) -> str:
    """A report sitting in the review queue."""
    created = post_report(client, idempotency_key=f"p5-review-{id(client)}-{_counter()}").json()
    set_status(created["id"], ReportStatus.FLAGGED)
    return created["id"]


_seq = 0


def _counter() -> int:
    global _seq
    _seq += 1
    return _seq


# --- The two decisions ------------------------------------------------------------------


def test_an_operator_can_verify_a_flagged_report(client: TestClient, flagged_report: str) -> None:
    response = client.post(
        f"{ENDPOINT}/{flagged_report}/review",
        json={"decision": "verify", "reviewer": "operator-priya", "note": "Confirmed by phone"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ReportStatus.VERIFIED.value
    assert body["reviewer"] == "operator-priya"

    assert client.get(f"{ENDPOINT}/{flagged_report}").json()["status"] == "verified"


def test_an_operator_can_reject_a_flagged_report(client: TestClient, flagged_report: str) -> None:
    """The only path in the whole system that reaches `rejected`."""
    response = client.post(
        f"{ENDPOINT}/{flagged_report}/review",
        json={"decision": "reject", "reviewer": "operator-arun", "note": "Prank call"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == ReportStatus.REJECTED.value


def test_the_decision_and_reviewer_are_recorded(client: TestClient, flagged_report: str) -> None:
    """FR-30: the human-in-the-loop claim must be evidenced in data."""
    client.post(
        f"{ENDPOINT}/{flagged_report}/review",
        json={"decision": "reject", "reviewer": "operator-arun", "note": "Duplicate of #412"},
    )

    reasons = client.get(f"{ENDPOINT}/{flagged_report}").json()["authenticity_reasons"]
    entry = next(item for item in reasons if item["code"].startswith("HUMAN_REVIEW"))

    assert entry["code"] == "HUMAN_REVIEW_REJECTED"
    assert entry["source"] == "operator:operator-arun"
    assert entry["note"] == "Duplicate of #412"


def test_review_does_not_move_the_computed_score(client: TestClient, flagged_report: str) -> None:
    """The operator overrides the routing, not the measurement."""
    before = client.get(f"{ENDPOINT}/{flagged_report}").json()["authenticity_score"]

    client.post(
        f"{ENDPOINT}/{flagged_report}/review",
        json={"decision": "verify", "reviewer": "operator-priya"},
    )

    assert client.get(f"{ENDPOINT}/{flagged_report}").json()["authenticity_score"] == before


def test_a_rejected_report_is_kept_not_deleted(client: TestClient, flagged_report: str) -> None:
    """Rejection is a status, never a deletion — the record survives for audit."""
    client.post(
        f"{ENDPOINT}/{flagged_report}/review",
        json={"decision": "reject", "reviewer": "operator-arun"},
    )

    detail = client.get(f"{ENDPOINT}/{flagged_report}")
    assert detail.status_code == 200
    assert detail.json()["text"]


# --- Guard rails --------------------------------------------------------------------------


def test_only_flagged_reports_can_be_reviewed(client: TestClient) -> None:
    created = post_report(client, idempotency_key=f"p5-not-flagged-{_counter()}").json()

    response = client.post(
        f"{ENDPOINT}/{created['id']}/review",
        json={"decision": "reject", "reviewer": "operator-arun"},
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "REPORT_NOT_UNDER_REVIEW"
    assert error["detail"]["status"] != "flagged"


def test_a_report_cannot_be_reviewed_twice(client: TestClient, flagged_report: str) -> None:
    first = client.post(
        f"{ENDPOINT}/{flagged_report}/review",
        json={"decision": "verify", "reviewer": "operator-priya"},
    )
    second = client.post(
        f"{ENDPOINT}/{flagged_report}/review",
        json={"decision": "reject", "reviewer": "operator-arun"},
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_reviewing_an_unknown_report_is_a_typed_error(client: TestClient) -> None:
    response = client.post(
        f"{ENDPOINT}/00000000-0000-0000-0000-000000000000/review",
        json={"decision": "verify", "reviewer": "operator-priya"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPORT_NOT_FOUND"


def test_a_reviewer_identity_is_required(client: TestClient, flagged_report: str) -> None:
    """An anonymous override is not an audit trail."""
    response = client.post(
        f"{ENDPOINT}/{flagged_report}/review", json={"decision": "verify"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_an_unknown_decision_is_rejected(client: TestClient, flagged_report: str) -> None:
    response = client.post(
        f"{ENDPOINT}/{flagged_report}/review",
        json={"decision": "delete", "reviewer": "operator-arun"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- The pipeline end to end ------------------------------------------------------------------


def test_ingestion_produces_a_trust_score_without_being_asked(client: TestClient) -> None:
    created = post_report(
        client,
        idempotency_key=f"p5-pipeline-{_counter()}",
        files={"image": ("p.jpg", BytesIO(jpeg_bytes(31)), "image/jpeg")},
    ).json()

    detail = client.get(f"{ENDPOINT}/{created['id']}").json()

    assert detail["authenticity_score"] is not None
    assert detail["authenticity_reasons"]
    assert detail["status"] in {"verified", "flagged"}


def test_a_resubmitted_photograph_is_flagged_end_to_end(client: TestClient) -> None:
    """The whole duplicate path, exercised through the API rather than the service.

    Placed far from every other test report on purpose. Other tests post at shared
    default coordinates, and a nearby cluster would corroborate the reshare (+25),
    lifting 15 to exactly the flag threshold and quietly testing two signals at once.
    """
    payload = jpeg_bytes(77)
    suffix = _counter()
    remote = {"lat": 8.5, "lng": 98.5}

    first = post_report(
        client,
        idempotency_key=f"p5-dup-original-{suffix}",
        reporter_pseudonym="first-reporter",
        **remote,
        files={"image": ("a.jpg", BytesIO(payload), "image/jpeg")},
    ).json()
    second = post_report(
        client,
        idempotency_key=f"p5-dup-reshare-{suffix}",
        reporter_pseudonym="second-reporter",
        **remote,
        files={"image": ("b.jpg", BytesIO(payload), "image/jpeg")},
    ).json()

    original = client.get(f"{ENDPOINT}/{first['id']}").json()
    reshare = client.get(f"{ENDPOINT}/{second['id']}").json()

    reshare_codes = [item["code"] for item in reshare["authenticity_reasons"]]
    original_codes = [item["code"] for item in original["authenticity_reasons"]]

    assert "DUPLICATE_IMAGE" in reshare_codes
    assert "DUPLICATE_IMAGE" not in original_codes
    assert reshare["status"] == "flagged"
    assert reshare["authenticity_score"] < original["authenticity_score"]
