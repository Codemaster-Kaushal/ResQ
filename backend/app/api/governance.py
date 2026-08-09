"""Governance: what this system is actually doing, as opposed to what it could do.

TRD §9 names over-claiming in the pitch as a project risk, and this endpoint is the
mitigation. It reports the provider that **actually scored the reports**, read back out
of the data, rather than the one configured in the environment. If every score came
from the local rule-based scorer, that is what it says — in a sentence, in plain
English, so nobody has to interpret a config dump.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.ai.base import LOCAL_PROVIDER_NAME
from app.ai.router import router as triage_router
from app.config import settings
from app.core.logging import get_logger
from app.core.runtime import uptime_seconds
from app.db import get_session
from app.models import Activity, ProcessEvent, Report, ReportStatus, Responder

logger = get_logger(__name__)

router = APIRouter(prefix="/api/governance", tags=["governance"])

# What each provider can actually do, so "vision" is never implied by a text model.
PROVIDER_CAPABILITIES: dict[str, list[str]] = {
    "gemini": ["text", "vision"],
    "groq": ["text"],
    "local_granite": ["text"],  # vision is wired but not enabled
    LOCAL_PROVIDER_NAME: ["text"],
}

PROVIDER_KINDS: dict[str, str] = {
    "gemini": "remote language model",
    "groq": "remote language model",
    "local_granite": "on-premise language model (IBM Granite via Ollama)",
    LOCAL_PROVIDER_NAME: "deterministic rule-based scorer",
}


class ProviderStatus(BaseModel):
    name: str
    kind: str
    model: str | None = None
    capabilities: list[str]
    credentials_configured: bool
    available: bool
    reports_scored: int = Field(description="How many reports this provider actually scored")


class ScoringProvenance(BaseModel):
    configured_order: list[str]
    providers: list[ProviderStatus]
    active_provider: str = Field(description="What is answering now, not what is configured")
    running_on_fallback: bool
    fallback_state: str
    honest_summary: str = Field(description="Plain-English statement, safe to read aloud")


class HumanInTheLoop(BaseModel):
    """FR-30: the human-in-the-loop claim, evidenced in data rather than asserted."""

    priority_overrides: int
    reports_verified_by_human: int
    reports_rejected_by_human: int
    assignments_rejected: int
    total_human_actions: int = Field(
        description="Sum of the four counts above — decisions, not routine dispatches"
    )
    operators_seen: list[str]


class DataSnapshot(BaseModel):
    reports: int
    reports_by_status: dict[str, int]
    responders: int
    process_events: int
    auto_rejected_reports: int = Field(
        description="Must always be zero: no automated path may reject a report (FR-15)"
    )


class GovernanceReport(BaseModel):
    service: dict[str, Any]
    scoring: ScoringProvenance
    thresholds: dict[str, Any]
    human_in_the_loop: HumanInTheLoop
    data: DataSnapshot


async def _provider_statuses(usage: Counter) -> list[ProviderStatus]:
    from app.ai.resq_engine import engine_provider

    availability = triage_router.available_providers()
    models = {
        "gemini": settings.gemini_model,
        "groq": settings.groq_model,
        "local_granite": settings.granite_model,
    }
    credentials = settings.configured_providers()

    names = list(settings.provider_order)
    if settings.ai_engine_enabled or usage.get("local_granite"):
        names.insert(0, "local_granite")
        credentials["local_granite"] = bool(settings.ai_engine_enabled)
        # The router's cheap check only asks "is it configured and importable?",
        # which stays true after Ollama dies. This endpoint exists to be honest
        # about what is really running, so it pays for a real round trip.
        availability["local_granite"] = (
            engine_provider.is_available() and await engine_provider.ollama_reachable()
        )

    return [
        ProviderStatus(
            name=name,
            kind=PROVIDER_KINDS.get(name, "unknown"),
            model=models.get(name),
            capabilities=PROVIDER_CAPABILITIES.get(name, ["text"]),
            credentials_configured=credentials.get(name, False),
            available=availability.get(name, False),
            reports_scored=usage.get(name, 0),
        )
        for name in names
    ]


def _summarise(usage: Counter, providers: list[ProviderStatus]) -> tuple[str, str, str, bool]:
    """Return (active_provider, fallback_state, honest_summary, running_on_fallback)."""
    # Granite runs on our own hardware, so it is neither the rule-based floor nor
    # a remote provider. Lumping it in with the remote ones made the summary
    # claim "remote providers are configured" on an installation that has no
    # network dependency at all.
    remote_ready = [
        p.name
        for p in providers
        if p.name not in (LOCAL_PROVIDER_NAME, "local_granite") and p.available
    ]
    granite = next((p for p in providers if p.name == "local_granite"), None)

    if usage:
        active = usage.most_common(1)[0][0]
    else:
        active = remote_ready[0] if remote_ready else LOCAL_PROVIDER_NAME

    on_fallback = active == LOCAL_PROVIDER_NAME
    scored_total = sum(usage.values())

    granite_scored = usage.get("local_granite", 0)

    if granite_scored:
        fallback_state = "on-premise language model in use"
        summary = (
            f"{granite_scored} of {scored_total} reports were scored by IBM Granite "
            f"({settings.granite_model}) running on-premise via Ollama; the rest by the "
            "local deterministic rule-based scorer. Every report is scored by the rules "
            "first so it can be queued immediately, and the model re-scores only where "
            "it could change the outcome. Image content is not analysed: the visual "
            "severity modifier is always zero."
        )
    elif not remote_ready:
        granite_up = bool(granite and granite.available)
        if settings.ai_engine_enabled and granite_up:
            fallback_state = "on-premise model reachable; no report has needed it yet"
            granite_note = (
                f" IBM Granite ({settings.granite_model}) is running on-premise and "
                "reachable, but no report has needed re-scoring yet: the rules were "
                "confident on every report so far, so the model was never consulted."
            )
        elif settings.ai_engine_enabled:
            fallback_state = "on-premise model enabled but unreachable"
            granite_note = (
                f" IBM Granite ({settings.granite_model}) is enabled but not answering — "
                "check that Ollama is running and the model is pulled. Scoring has "
                "continued on the rules, so no report was lost."
            )
        else:
            fallback_state = "no remote provider credentials configured"
            granite_note = (
                " No remote model credentials are configured, so no report has been "
                "scored by a language model."
            )
        summary = (
            "All scoring is performed by the local deterministic rule-based scorer."
            + granite_note
            + " Image content is not analysed: the visual severity modifier is always zero."
        )
    elif on_fallback and scored_total:
        fallback_state = "remote providers configured but the local scorer is answering"
        summary = (
            f"Remote providers are configured ({', '.join(remote_ready)}) but scoring has "
            "fallen back to the local rule-based scorer — the remote calls are failing, "
            "timing out, or rate limited. Scores remain valid; they are rule-based."
        )
    else:
        fallback_state = "remote provider answering"
        breakdown = ", ".join(f"{name}: {count}" for name, count in usage.most_common())
        summary = (
            f"Reports have been scored by: {breakdown}. The local rule-based scorer "
            "remains the fallback and answers whenever a remote provider fails."
        )

    return active, fallback_state, summary, on_fallback


@router.get(
    "",
    response_model=GovernanceReport,
    summary="Model provenance, thresholds, and the human-in-the-loop record",
)
async def governance(session: Session = Depends(get_session)) -> GovernanceReport:
    """What is really running (FR-29), and what humans have really decided (FR-30)."""
    reports = session.exec(select(Report)).all()
    events = session.exec(select(ProcessEvent)).all()

    usage = Counter(r.scoring_provider for r in reports if r.scoring_provider)
    providers = await _provider_statuses(usage)
    active, fallback_state, summary, on_fallback = _summarise(usage, providers)

    # Only events a person actually caused. REPORT_VERIFIED is emitted both by
    # automated authenticity scoring and by an operator clearing a flagged report, so
    # counting the activity alone would inflate the very claim this endpoint exists to
    # keep honest.
    human_events = [event for event in events if event.resource.startswith("operator:")]
    by_human_activity = Counter(event.activity for event in human_events)
    operators = sorted({event.resource.split(":", 1)[1] for event in human_events})

    # A responder declining an assignment is a human decision too, though the actor is
    # a crew rather than a control-room operator.
    assignment_rejections = sum(
        1 for event in events if event.activity == Activity.ASSIGNMENT_REJECTED.value
    )

    # The sum of the named categories below, not of every operator-tagged event. An
    # operator pressing "dispatch" is a person acting, but FR-30's claim is specifically
    # about overrides and reviews, and a total that quietly includes routine dispatches
    # would overstate it. Every number here can be reconciled from the fields beside it.
    human_actions = (
        by_human_activity.get(Activity.PRIORITY_OVERRIDDEN.value, 0)
        + by_human_activity.get(Activity.REPORT_VERIFIED.value, 0)
        + by_human_activity.get(Activity.REPORT_REJECTED.value, 0)
        + assignment_rejections
    )

    rejected_reports = [r for r in reports if r.status == ReportStatus.REJECTED]
    human_rejected = {
        event.case_id
        for event in events
        if event.activity == Activity.REPORT_REJECTED.value
    }
    auto_rejected = [r for r in rejected_reports if r.id not in human_rejected]

    return GovernanceReport(
        service={
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "uptime_seconds": uptime_seconds(),
            "database_dialect": settings.database_url.split(":", 1)[0],
            "debug_routes_enabled": settings.enable_debug_routes,
        },
        scoring=ScoringProvenance(
            configured_order=settings.provider_order,
            providers=providers,
            active_provider=active,
            running_on_fallback=on_fallback,
            fallback_state=fallback_state,
            honest_summary=summary,
        ),
        thresholds=settings.redacted_summary()["thresholds"],
        human_in_the_loop=HumanInTheLoop(
            priority_overrides=by_human_activity.get(Activity.PRIORITY_OVERRIDDEN.value, 0),
            reports_verified_by_human=by_human_activity.get(Activity.REPORT_VERIFIED.value, 0),
            reports_rejected_by_human=by_human_activity.get(Activity.REPORT_REJECTED.value, 0),
            assignments_rejected=assignment_rejections,
            total_human_actions=human_actions,
            operators_seen=operators,
        ),
        data=DataSnapshot(
            reports=len(reports),
            reports_by_status=dict(Counter(r.status.value for r in reports)),
            responders=len(session.exec(select(Responder)).all()),
            process_events=len(events),
            auto_rejected_reports=len(auto_rejected),
        ),
    )
