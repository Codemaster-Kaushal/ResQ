"""Phase 10: the governance endpoint (FR-29, FR-30).

Acceptance: governance reports the *true* active provider.

TRD §9 names over-claiming in the pitch as a risk and this endpoint as the mitigation,
so these tests are mostly about it refusing to flatter the system.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.models import Report, ReportStatus
from seed import seed as seed_module

GOVERNANCE = "/api/governance"


@pytest.fixture(scope="module", autouse=True)
def seeded_world():
    """Own the dataset rather than inheriting whatever the previous module left.

    Governance reports counts read back out of the data, so these assertions are only
    meaningful against a known population.
    """
    seed_module.run(reset=True, echo=lambda _line: None)


@pytest.fixture
def report(client: TestClient, seeded_world) -> dict:
    return client.get(GOVERNANCE).json()


# --- Provenance: what actually scored the reports -----------------------------------------


def test_the_active_provider_is_read_from_the_data(report: dict) -> None:
    """Not from configuration. With no keys set, everything was scored locally."""
    scoring = report["scoring"]

    assert scoring["active_provider"] == "local"
    assert scoring["running_on_fallback"] is True

    local = next(p for p in scoring["providers"] if p["name"] == "local")
    assert local["reports_scored"] > 0


def test_unconfigured_providers_report_zero(report: dict) -> None:
    for provider in report["scoring"]["providers"]:
        if provider["name"] != "local":
            assert provider["credentials_configured"] is False
            assert provider["available"] is False
            assert provider["reports_scored"] == 0


def test_the_summary_says_plainly_that_no_model_was_used(report: dict) -> None:
    """The sentence a judge might hear read aloud. It must not imply an LLM ran."""
    summary = report["scoring"]["honest_summary"].lower()

    assert "local" in summary
    assert "no remote model credentials are configured" in summary
    # And it volunteers the limitation nobody would think to ask about.
    assert "image content is not analysed" in summary


def test_capabilities_do_not_imply_vision_where_there_is_none(report: dict) -> None:
    capabilities = {p["name"]: p["capabilities"] for p in report["scoring"]["providers"]}

    assert "vision" in capabilities["gemini"]
    assert "vision" not in capabilities["groq"]
    assert "vision" not in capabilities["local"]


def test_the_configured_order_is_reported(report: dict) -> None:
    assert report["scoring"]["configured_order"] == settings.provider_order
    assert report["scoring"]["configured_order"][-1] == "local"


def test_the_fallback_state_is_named(report: dict) -> None:
    assert report["scoring"]["fallback_state"] == "no remote provider credentials configured"


def test_configuring_a_key_changes_what_is_reported(
    client: TestClient, seeded_world, monkeypatch
) -> None:
    """Availability tracks configuration, but the scored counts still come from data."""
    monkeypatch.setattr(settings, "gemini_api_key", "a-key")

    scoring = client.get(GOVERNANCE).json()["scoring"]
    gemini = next(p for p in scoring["providers"] if p["name"] == "gemini")

    assert gemini["credentials_configured"] is True
    assert gemini["available"] is True
    # It is configured, but it has still never scored anything.
    assert gemini["reports_scored"] == 0
    assert scoring["active_provider"] == "local"
    # It says so out loud rather than letting a configured key imply the model ran.
    assert "fallen back to the local" in scoring["honest_summary"].lower()
    assert scoring["fallback_state"] == (
        "remote providers configured but the local scorer is answering"
    )


# --- Thresholds -------------------------------------------------------------------------------


def test_the_thresholds_in_use_are_exposed(report: dict) -> None:
    thresholds = report["thresholds"]

    assert thresholds["authenticity_flag_threshold"] == settings.authenticity_flag_threshold
    assert thresholds["phash_duplicate_distance"] == settings.phash_duplicate_distance
    assert thresholds["dispatch_max_radius_km"] == settings.dispatch_max_radius_km
    assert thresholds["bottleneck_deviation_ratio"] == settings.bottleneck_deviation_ratio


def test_no_secret_is_ever_returned(client: TestClient, seeded_world, monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "super-secret-value")
    monkeypatch.setattr(settings, "groq_api_key", "another-secret")

    body = client.get(GOVERNANCE).text

    assert "super-secret-value" not in body
    assert "another-secret" not in body


# --- FR-30: the human-in-the-loop record ---------------------------------------------------------


def test_human_actions_are_counted_from_the_event_log(client: TestClient, seeded_world) -> None:
    before = client.get(GOVERNANCE).json()["human_in_the_loop"]

    queued = client.get("/api/queue", params={"limit": 1}).json()["items"][0]
    client.post(
        f"/api/queue/{queued['id']}/override",
        json={"action": "pin", "operator": "controller-gov-test", "reason": "audit trail"},
    )

    after = client.get(GOVERNANCE).json()["human_in_the_loop"]

    assert after["priority_overrides"] == before["priority_overrides"] + 1
    assert after["total_human_actions"] > before["total_human_actions"]
    assert "controller-gov-test" in after["operators_seen"]

    client.post(
        f"/api/queue/{queued['id']}/override",
        json={"action": "clear", "operator": "controller-gov-test"},
    )


def test_automated_verifications_are_not_counted_as_human_ones(
    client: TestClient, seeded_world
) -> None:
    """REPORT_VERIFIED is emitted by authenticity scoring *and* by human review.
    Counting the activity alone would inflate the claim this endpoint exists to keep
    honest — every seeded report was verified automatically."""
    human = client.get(GOVERNANCE).json()["human_in_the_loop"]

    verified_reports = client.get("/api/reports", params={"status": "queued", "limit": 1}).json()

    assert verified_reports["total"] > 0
    assert human["reports_verified_by_human"] < verified_reports["total"]


def test_a_human_review_is_recorded(client: TestClient, seeded_world) -> None:
    flagged = client.get("/api/reports", params={"status": "flagged", "limit": 1}).json()["items"]
    if not flagged:
        pytest.skip("no flagged report available in this run")

    before = client.get(GOVERNANCE).json()["human_in_the_loop"]
    client.post(
        f"/api/reports/{flagged[0]['id']}/review",
        json={"decision": "verify", "reviewer": "operator-gov-test"},
    )
    after = client.get(GOVERNANCE).json()["human_in_the_loop"]

    assert after["reports_verified_by_human"] == before["reports_verified_by_human"] + 1
    assert "operator-gov-test" in after["operators_seen"]


# --- FR-15, evidenced ------------------------------------------------------------------------------


def test_the_total_is_the_sum_of_the_named_categories(report: dict) -> None:
    """A total that quietly folded in routine dispatches would overstate FR-30's claim."""
    human = report["human_in_the_loop"]

    assert human["total_human_actions"] == (
        human["priority_overrides"]
        + human["reports_verified_by_human"]
        + human["reports_rejected_by_human"]
        + human["assignments_rejected"]
    )


def test_a_freshly_seeded_system_claims_no_human_decisions(client: TestClient, seeded_world) -> None:
    """The seed simulates operator-dispatched history, but nobody has overridden or
    reviewed anything — and the endpoint must say so rather than counting the
    dispatches."""
    seed_module.run(reset=True, echo=lambda _line: None)

    human = client.get(GOVERNANCE).json()["human_in_the_loop"]

    assert human["total_human_actions"] == 0
    assert human["priority_overrides"] == 0
    assert human["reports_verified_by_human"] == 0


def test_nothing_was_auto_rejected(report: dict) -> None:
    """The strongest form of the claim: measured, not asserted."""
    assert report["data"]["auto_rejected_reports"] == 0


def test_an_auto_rejection_would_be_caught(client: TestClient, seeded_world) -> None:
    """Prove the check has teeth by faking exactly what it is meant to detect."""
    with Session(engine) as session:
        victim = session.exec(
            select(Report).where(Report.status == ReportStatus.QUEUED)
        ).first()
        original = victim.status
        victim_id = victim.id
        victim.status = ReportStatus.REJECTED  # no human review event accompanies this
        session.add(victim)
        session.commit()

    try:
        assert client.get(GOVERNANCE).json()["data"]["auto_rejected_reports"] == 1
    finally:
        with Session(engine) as session:
            restored = session.get(Report, victim_id)
            restored.status = original
            session.add(restored)
            session.commit()

    assert client.get(GOVERNANCE).json()["data"]["auto_rejected_reports"] == 0


# --- Service and data snapshot -----------------------------------------------------------------------


def test_the_service_block_describes_the_deployment(report: dict) -> None:
    service = report["service"]

    assert service["version"] == settings.app_version
    assert service["database_dialect"] == "sqlite"
    assert service["uptime_seconds"] >= 0
    assert isinstance(service["debug_routes_enabled"], bool)


def test_the_data_snapshot_matches_reality(client: TestClient, report: dict) -> None:
    total = client.get("/api/reports", params={"limit": 1}).json()["total"]
    events = client.get("/api/events", params={"limit": 1}).json()["total"]

    assert report["data"]["reports"] == total
    assert report["data"]["process_events"] == events
    assert sum(report["data"]["reports_by_status"].values()) == total
