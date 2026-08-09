"""Integration with the ResQ AI engine (IBM Granite via Ollama).

Two things these tests are really about:

1. **The queue never waits on the model.** Pass 1 (rules) always runs, so a
   report is ranked and dispatchable before Granite is even consulted.
2. **Failure is invisible to the citizen.** A missing engine, an unreachable
   Ollama, a timeout or a bug inside the engine must all leave the rule-based
   score standing rather than costing the report.

Nothing here requires Ollama to be running: the engine is driven through fakes
so the behaviour is asserted deterministically. The live path is exercised
separately, by hand, against a real model.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import inspect
import io

import pytest
from sqlmodel import Session, select

from app.ai.resq_engine import EngineAuthenticity, EngineSeverity, ResqAIEngine
from app.config import settings
from app.core.time import utcnow
from app.db import engine
from app.models import Activity, IncidentType, ProcessEvent, Report, ReportStatus
from app.services import triage as triage_service
from app.services.ai_state import known_hashes_for, previous_reports_for
from app.services.triage import needs_ai_rescore, rescore_with_ai

# Most tests here drive async services. The contract tests at the bottom are
# plain functions and opt out individually with @pytest.mark.asyncio(False).
pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module", autouse=True)
def schema():
    """This module never touches the app, so nothing else creates the tables."""
    from app.db import init_db

    init_db()


def make_report(session: Session, **overrides) -> Report:
    defaults = dict(
        idempotency_key=f"ai-{uuid.uuid4()}",
        text="a wall has collapsed and people are trapped",
        lat=12.9352,
        lng=77.6245,
        client_created_at=utcnow(),
        received_at=utcnow(),
        reporter_pseudonym="ai-test-reporter",
        incident_type=IncidentType.STRUCTURAL_COLLAPSE,
        severity_score=50,
        severity_reasons=[{"code": "X", "weight": 50, "source": "taxonomy"}],
        authenticity_score=60,
        status=ReportStatus.QUEUED,
        scoring_provider="local",
    )
    defaults.update(overrides)
    report = Report(**defaults)
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


class FakeEngine:
    """Stands in for the AI engine so behaviour is deterministic."""

    name = "local_granite"

    def __init__(self, *, available=True, verdict=None, blow_up=False):
        self._available = available
        self._verdict = verdict
        self._blow_up = blow_up
        self.calls = 0

    def is_available(self):
        return self._available

    async def score(self, text):
        self.calls += 1
        if self._blow_up:
            raise RuntimeError("granite exploded")
        return self._verdict


def granite_verdict(score=88, incident=IncidentType.TRAPPED_PERSONS):
    return EngineSeverity(
        score=score,
        label="CRITICAL",
        reasons=[
            {"code": "TRAPPED_PERSONS", "weight": None, "source": "local_granite"},
            {"code": "PEOPLE_AT_RISK", "weight": None, "source": "local_granite"},
        ],
        incident_type=incident,
        confidence=0.91,
        provider="local_granite",
        fallback_state="NORMAL",
        model="granite3.3:8b",
    )


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setattr(settings, "ai_engine_enabled", True)


# --- Selective escalation -------------------------------------------------


async def test_confident_rules_do_not_call_the_model(ai_on) -> None:
    """The whole point of escalating selectively: most reports never reach it."""
    with Session(engine) as session:
        report = make_report(session, severity_score=50, incident_type=IncidentType.FIRE)

    wanted, why = needs_ai_rescore(report)

    assert wanted is False
    assert "confident" in why


async def test_unrecognised_text_escalates(ai_on) -> None:
    """`other` means the rules did not understand it — a non-English report today."""
    with Session(engine) as session:
        report = make_report(session, incident_type=IncidentType.OTHER, severity_score=10)

    wanted, why = needs_ai_rescore(report)

    assert wanted is True
    assert "other" in why


@pytest.mark.parametrize("score", [38, 40, 42, 58, 62, 78, 82])
async def test_scores_near_a_band_boundary_escalate(ai_on, score: int) -> None:
    """The band is what an operator acts on, so the edges are worth a second look."""
    with Session(engine) as session:
        report = make_report(session, severity_score=score, incident_type=IncidentType.FIRE)

    assert needs_ai_rescore(report)[0] is True


async def test_nothing_escalates_when_the_engine_is_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_engine_enabled", False)
    with Session(engine) as session:
        report = make_report(session, incident_type=IncidentType.OTHER)

    assert needs_ai_rescore(report)[0] is False


async def test_a_report_is_not_rescored_twice(ai_on) -> None:
    with Session(engine) as session:
        report = make_report(
            session, incident_type=IncidentType.OTHER, scoring_provider="local_granite"
        )

    assert needs_ai_rescore(report)[0] is False


# --- Pass 2 replaces the score ---------------------------------------------


async def test_the_model_replaces_the_rule_based_score(ai_on, monkeypatch) -> None:
    fake = FakeEngine(verdict=granite_verdict(score=88))
    monkeypatch.setattr("app.ai.resq_engine.engine_provider", fake)

    with Session(engine) as session:
        report = make_report(session, incident_type=IncidentType.OTHER, severity_score=10)
        report_id = report.id

    assert await rescore_with_ai(report_id) is True

    with Session(engine) as session:
        updated = session.get(Report, report_id)

    assert updated.severity_score == 88
    assert updated.incident_type == IncidentType.TRAPPED_PERSONS
    assert updated.scoring_provider == "local_granite"
    assert [r["code"] for r in updated.severity_reasons] == ["TRAPPED_PERSONS", "PEOPLE_AT_RISK"]


async def test_model_reasons_carry_no_weights(ai_on, monkeypatch) -> None:
    """Granite emits bare codes. Inventing weights so they sum to the score would
    be a lie; the UI hides its total instead."""
    monkeypatch.setattr(
        "app.ai.resq_engine.engine_provider", FakeEngine(verdict=granite_verdict())
    )
    with Session(engine) as session:
        report_id = make_report(session, incident_type=IncidentType.OTHER).id

    await rescore_with_ai(report_id)

    with Session(engine) as session:
        updated = session.get(Report, report_id)

    assert all(r["weight"] is None for r in updated.severity_reasons)


async def test_rescoring_is_audited(ai_on, monkeypatch) -> None:
    """A score that changes under an operator's cursor must be explainable."""
    monkeypatch.setattr(
        "app.ai.resq_engine.engine_provider", FakeEngine(verdict=granite_verdict(score=88))
    )
    with Session(engine) as session:
        report_id = make_report(session, incident_type=IncidentType.OTHER, severity_score=10).id

    await rescore_with_ai(report_id)

    with Session(engine) as session:
        event = session.exec(
            select(ProcessEvent)
            .where(ProcessEvent.case_id == report_id)
            .where(ProcessEvent.activity == Activity.AI_RESCORED.value)
        ).one()

    assert event.event_metadata["severity_before"] == 10
    assert event.event_metadata["severity_after"] == 88
    assert event.event_metadata["model"] == "granite3.3:8b"
    assert "other" in event.event_metadata["escalated_because"]


# --- Failure never costs the report ----------------------------------------


async def test_an_unavailable_engine_leaves_the_score_standing(ai_on, monkeypatch) -> None:
    monkeypatch.setattr("app.ai.resq_engine.engine_provider", FakeEngine(available=False))
    with Session(engine) as session:
        report_id = make_report(session, incident_type=IncidentType.OTHER, severity_score=10).id

    assert await rescore_with_ai(report_id) is False

    with Session(engine) as session:
        assert session.get(Report, report_id).severity_score == 10


async def test_a_crashing_engine_leaves_the_score_standing(ai_on, monkeypatch) -> None:
    monkeypatch.setattr("app.ai.resq_engine.engine_provider", FakeEngine(blow_up=True))
    with Session(engine) as session:
        report_id = make_report(session, incident_type=IncidentType.OTHER, severity_score=10).id

    assert await rescore_with_ai(report_id) is False

    with Session(engine) as session:
        report = session.get(Report, report_id)
    assert report.severity_score == 10
    assert report.scoring_provider == "local"


async def test_a_missing_report_is_not_an_error(ai_on, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ai.resq_engine.engine_provider", FakeEngine(verdict=granite_verdict())
    )

    assert await rescore_with_ai(uuid.uuid4()) is False


# --- The engine reads OUR database, not its own state.json ------------------


async def test_known_hashes_come_from_the_database(ai_on) -> None:
    """The engine's own state.json only knows reports that passed through it, so
    it would miss the seeded duplicate pair entirely."""
    with Session(engine) as session:
        earlier = make_report(
            session,
            image_phash="c0f038cee30f1f33",
            received_at=utcnow() - timedelta(minutes=30),
            reporter_pseudonym="first-reporter",
        )
        later = make_report(
            session,
            image_phash="c0f038cee30f1f31",
            received_at=utcnow(),
            reporter_pseudonym="second-reporter",
        )

        hashes = known_hashes_for(session, later)

    assert str(earlier.id) in hashes
    # The original must not be told about the report that copied it.
    assert str(later.id) not in hashes


async def test_prior_reports_are_scoped_to_the_same_pseudonym(ai_on) -> None:
    with Session(engine) as session:
        make_report(session, reporter_pseudonym="roamer", lat=19.07, lng=72.87)
        make_report(session, reporter_pseudonym="someone-else", lat=13.0, lng=77.5)
        subject = make_report(session, reporter_pseudonym="roamer")

        priors = previous_reports_for(session, subject)

    assert len(priors) == 1
    assert priors[0].lat == pytest.approx(19.07)


# --- Availability ------------------------------------------------------------


async def test_the_adapter_is_unavailable_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_engine_enabled", False)

    assert ResqAIEngine().is_available() is False


async def test_the_adapter_is_unavailable_when_the_engine_is_missing(monkeypatch) -> None:
    """A wrong path must degrade, not crash the backend at import time."""
    from app.ai import resq_engine

    monkeypatch.setattr(settings, "ai_engine_enabled", True)
    monkeypatch.setattr(settings, "ai_engine_path", "/nonexistent/ai-engine")
    resq_engine._load_engine.cache_clear()
    try:
        assert resq_engine.ResqAIEngine().is_available() is False
    finally:
        resq_engine._load_engine.cache_clear()
