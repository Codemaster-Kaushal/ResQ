"""Triage: classification and severity scoring (TRD §4.1).

    severity = clamp(incident_weight + life_risk_signal
                     + people_affected + vulnerability_signal
                     + visual_severity_modifier, 0, 100)

Two invariants hold for every score produced here:

1. **Every score is explained.** A score with no reason codes is a failed requirement
   (FR-8), so each contributing component appends its own reason.
2. **The reason weights sum to the score.** Capping and clamping are themselves
   recorded, so a reader can always add the reasons up and land on the number. Without
   that, "explainable" degrades to "annotated".

Weighting lives here rather than in a provider, so the arithmetic is identical whether
Gemini, Groq, or the local rules extracted the signals.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlmodel import Session, select

from app.ai.base import TriageResult
from app.ai.router import RoutedTriage, TriageRouter, router as default_router
from app.config import settings
from app.core.logging import get_logger
from app.db import engine
from app.models import Activity, IncidentType, Report, ReportStatus
from app.services.events import emit_event

logger = get_logger(__name__)

# --- Weight tables (TRD §4.1) ------------------------------------------------------

INCIDENT_WEIGHTS: dict[IncidentType, int] = {
    IncidentType.TRAPPED_PERSONS: 40,
    IncidentType.STRUCTURAL_COLLAPSE: 38,
    IncidentType.MEDICAL: 32,
    IncidentType.FIRE: 30,
    IncidentType.FLOODING: 26,
    IncidentType.LANDSLIDE: 26,
    IncidentType.INFRASTRUCTURE: 14,
    IncidentType.OTHER: 10,
}

LIFE_RISK_WEIGHTS: dict[str, int] = {
    "not_breathing": 14,
    "drowning": 14,
    "unconscious": 12,
    "trapped": 12,
    "bleeding": 10,
    "rising_water": 8,
    "no_exit": 8,
}
LIFE_RISK_CAP = 30

VULNERABILITY_WEIGHTS: dict[str, int] = {
    "children": 6,
    "elderly": 6,
    "disabled": 6,
    "pregnant": 6,
    "injured": 5,
}
VULNERABILITY_CAP = 15

# (upper bound inclusive, weight, code suffix)
PEOPLE_BANDS: tuple[tuple[int, int, str], ...] = (
    (1, 3, "1"),
    (5, 7, "2_5"),
    (20, 12, "6_20"),
    (10**9, 15, "20_PLUS"),
)

SEVERITY_MIN, SEVERITY_MAX = 0, 100

# Remote models return free text; map it onto the canonical vocabulary the weight
# tables use. Anything unrecognised is dropped — an unknown term has no defensible
# weight, and inventing one would put an unexplainable number into the score.
_LIFE_RISK_SYNONYMS: dict[str, tuple[str, ...]] = {
    "not_breathing": ("not breathing", "notbreathing", "no breathing", "apnea", "asphyxia"),
    "drowning": ("drowning", "drown"),
    "unconscious": ("unconscious", "unresponsive", "passed out"),
    "trapped": ("trapped", "buried", "pinned", "stuck"),
    "bleeding": ("bleeding", "haemorrhage", "hemorrhage", "blood loss"),
    "rising_water": ("rising water", "risingwater", "water rising", "water is rising", "water level"),
    "no_exit": ("no exit", "noexit", "no way out", "exit blocked", "trapped inside"),
}

_VULNERABILITY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "children": ("child", "children", "kid", "baby", "infant", "minor", "toddler"),
    "elderly": ("elderly", "old", "senior", "aged"),
    "disabled": ("disabled", "wheelchair", "handicapped", "immobile"),
    "pregnant": ("pregnant", "labour", "labor", "expecting"),
    "injured": ("injured", "wounded", "hurt", "fracture"),
}


def _canonicalise(terms: Iterable[str], synonyms: dict[str, tuple[str, ...]]) -> list[str]:
    resolved: list[str] = []
    for raw in terms:
        needle = str(raw).strip().lower().replace("-", "_")
        if needle in synonyms and needle not in resolved:
            resolved.append(needle)
            continue
        for canonical, variants in synonyms.items():
            if canonical in resolved:
                continue
            if any(variant in needle for variant in variants):
                resolved.append(canonical)
                break
    return resolved


def canonical_life_risk(terms: Iterable[str]) -> list[str]:
    return _canonicalise(terms, _LIFE_RISK_SYNONYMS)


def canonical_vulnerability(terms: Iterable[str]) -> list[str]:
    return _canonicalise(terms, _VULNERABILITY_SYNONYMS)


# --- Severity ------------------------------------------------------------------------


def reason(code: str, weight: int, source: str) -> dict[str, Any]:
    return {"code": code, "weight": weight, "source": source}


@dataclass
class Severity:
    score: int
    reasons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reason_codes(self) -> list[str]:
        return [item["code"] for item in self.reasons]


def _capped_terms(
    terms: Iterable[str],
    weights: dict[str, int],
    cap: int,
    prefix: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Accumulate term weights up to a cap, heaviest first.

    The final term is truncated rather than dropped when it crosses the cap, so the
    reasons still add up to the component total exactly.
    """
    ordered = sorted(
        {term for term in terms if term in weights},
        key=lambda term: (-weights[term], term),
    )

    total = 0
    reasons: list[dict[str, Any]] = []
    for term in ordered:
        if total >= cap:
            break
        applied = min(weights[term], cap - total)
        total += applied
        reasons.append(reason(f"{prefix}_{term.upper()}", applied, "text"))

    return total, reasons


def people_band(count: int | None) -> tuple[int, str] | None:
    if count is None or count < 1:
        return None
    for upper, weight, suffix in PEOPLE_BANDS:
        if count <= upper:
            return weight, suffix
    return None


def compute_severity(result: TriageResult, has_image: bool) -> Severity:
    """Apply TRD §4.1 to one provider's extraction."""
    reasons: list[dict[str, Any]] = []

    incident = result.incident_type or IncidentType.OTHER
    incident_weight = INCIDENT_WEIGHTS[incident]
    reasons.append(reason(f"INCIDENT_{incident.value.upper()}", incident_weight, "taxonomy"))

    life_risk_total, life_risk_reasons = _capped_terms(
        canonical_life_risk(result.life_risk_terms),
        LIFE_RISK_WEIGHTS,
        LIFE_RISK_CAP,
        "LIFE_RISK",
    )
    reasons.extend(life_risk_reasons)

    people_total = 0
    band = people_band(result.people_affected_estimate)
    if band is not None:
        people_total, suffix = band
        reasons.append(reason(f"PEOPLE_AFFECTED_{suffix}", people_total, "text"))

    vulnerability_total, vulnerability_reasons = _capped_terms(
        canonical_vulnerability(result.vulnerability_terms),
        VULNERABILITY_WEIGHTS,
        VULNERABILITY_CAP,
        "VULNERABILITY",
    )
    reasons.extend(vulnerability_reasons)

    # Absent image → modifier 0, no failure (TRD §4.1).
    modifier = result.visual_severity_modifier if has_image else 0
    if modifier:
        code = "IMAGE_CORROBORATION" if modifier > 0 else "IMAGE_CONTRADICTION"
        reasons.append(reason(code, modifier, "image"))

    raw = incident_weight + life_risk_total + people_total + vulnerability_total + modifier
    score = max(SEVERITY_MIN, min(SEVERITY_MAX, raw))

    # Record the clamp so the reasons still sum to the score.
    if score != raw:
        adjustment = score - raw
        code = "SCORE_CLAMPED_AT_MAX" if adjustment < 0 else "SCORE_CLAMPED_AT_MIN"
        reasons.append(reason(code, adjustment, "system"))

    return Severity(score=score, reasons=reasons)


# --- Orchestration ---------------------------------------------------------------------


def _image_bytes(report: Report) -> bytes | None:
    if not report.image_path:
        return None
    path = settings.media_dir / report.image_path
    try:
        return path.read_bytes()
    except OSError:
        # A missing file must not stop the report being scored from its text.
        logger.warning(
            "report image unreadable; scoring from text alone",
            extra={"report_id": str(report.id), "path": report.image_path},
        )
        return None


def apply_triage(report: Report, routed: RoutedTriage) -> Severity:
    """Write a routed result onto a report. Does not commit."""
    severity = compute_severity(routed.result, has_image=report.image_path is not None)

    report.incident_type = routed.result.incident_type
    report.severity_score = severity.score
    # Reassigned, never mutated in place: SQLAlchemy does not track JSON mutation.
    report.severity_reasons = severity.reasons
    report.scoring_provider = routed.provider

    if report.status == ReportStatus.RECEIVED:
        report.status = ReportStatus.CLASSIFIED

    return severity


async def triage_report(
    report_id: uuid.UUID,
    *,
    force: bool = False,
    triage_router: TriageRouter | None = None,
) -> Severity | None:
    """Score one report. Safe to call from a background task — it never raises.

    FR-4: a scoring problem may delay a score, but must never cost the report.
    """
    active_router = triage_router or default_router

    try:
        with Session(engine) as session:
            report = session.get(Report, report_id)
            if report is None:
                logger.warning("triage skipped: report not found", extra={"report_id": str(report_id)})
                return None

            if report.severity_score is not None and not force:
                return None

            routed = await active_router.classify(report.text, _image_bytes(report))
            severity = apply_triage(report, routed)

            session.add(report)
            emit_event(
                session,
                case_id=report.id,
                activity=Activity.TRIAGE_COMPLETED,
                resource=f"scorer:{routed.provider}",
                metadata={
                    "incident_type": routed.result.incident_type.value,
                    "severity_score": severity.score,
                    "reason_codes": severity.reason_codes,
                    "provider": routed.provider,
                    "degraded": routed.degraded,
                },
            )
            session.commit()

        logger.info(
            "report triaged",
            extra={
                "report_id": str(report_id),
                "incident_type": routed.result.incident_type.value,
                "severity_score": severity.score,
                "provider": routed.provider,
                "degraded": routed.degraded,
                "reason_codes": severity.reason_codes,
            },
        )
        return severity

    except Exception:  # noqa: BLE001 — a background task must never take the app down
        logger.exception(
            "triage failed; report left unscored for retry", extra={"report_id": str(report_id)}
        )
        return None


# --- Two-pass scoring: Granite re-scores, selectively --------------------------------
#
# Granite 8B is ~15 s per report on CPU. If scoring blocked the queue, a
# mass-casualty event would produce reports faster than they could be ranked and
# severity-ordered dispatch would stop meaning anything.
#
# So Pass 1 (the deterministic scorer above) always runs and always wins the
# race: the report is queued and dispatchable in milliseconds. Pass 2 runs
# afterwards in the background, and only on reports where a better answer could
# actually change what an operator does.

SEVERITY_BANDS = (40, 60, 80)


def needs_ai_rescore(report: Report) -> tuple[bool, str]:
    """Should Granite look at this report? Returns (decision, why)."""
    if not settings.ai_engine_enabled:
        return False, "engine disabled"
    if report.scoring_provider == "local_granite":
        return False, "already scored by the model"
    if report.severity_score is None:
        return False, "not yet scored by the rules"

    # The rules did not recognise the incident at all. This is where the model
    # earns its keep, and it is exactly what a non-English report hits today.
    if settings.ai_escalate_on_other and report.incident_type == IncidentType.OTHER:
        return True, "rules returned 'other'"

    # A score sitting next to a band boundary is one an operator acts on
    # differently depending which side it lands.
    if settings.ai_escalate_near_band:
        margin = settings.ai_escalate_band_margin
        if any(abs(report.severity_score - band) <= margin for band in SEVERITY_BANDS):
            return True, "score is near a band boundary"

    return False, "rules are confident and the band is clear"


async def rescore_with_ai(report_id: uuid.UUID) -> bool:
    """Pass 2. Replace the rule-based score with Granite's. Never raises.

    Returns True when the model actually re-scored the report.
    """
    from app.ai.resq_engine import engine_provider

    if not engine_provider.is_available():
        return False

    try:
        with Session(engine) as session:
            report = session.get(Report, report_id)
            if report is None:
                return False

            wanted, why = needs_ai_rescore(report)
            if not wanted:
                logger.debug(
                    "AI re-score skipped",
                    extra={"report_id": str(report_id), "reason": why},
                )
                return False

            before = report.severity_score
            verdict = await engine_provider.score(report.text)

            report.incident_type = verdict.incident_type
            report.severity_score = verdict.score
            report.severity_reasons = verdict.reasons
            report.scoring_provider = verdict.provider or "local_granite"
            session.add(report)

            emit_event(
                session,
                case_id=report.id,
                activity=Activity.AI_RESCORED,
                resource=f"scorer:{verdict.provider}",
                metadata={
                    "escalated_because": why,
                    "severity_before": before,
                    "severity_after": verdict.score,
                    "severity_label": verdict.label,
                    "incident_type": verdict.incident_type.value,
                    "model": verdict.model,
                    "confidence": verdict.confidence,
                    "fallback_state": verdict.fallback_state,
                },
            )
            session.commit()

        logger.info(
            "report re-scored by the model",
            extra={
                "report_id": str(report_id),
                "severity_before": before,
                "severity_after": verdict.score,
                "model": verdict.model,
                "escalated_because": why,
            },
        )
        return True

    except Exception:
        # Pass 1's score stands. A failed re-score costs precision, never the report.
        logger.exception(
            "AI re-score failed; the rule-based score stands",
            extra={"report_id": str(report_id)},
        )
        return False


async def rescore_pending(limit: int | None = None) -> int:
    """Sweep the queue for reports worth a second look from the model.

    Also covers the top of the queue: whatever is about to be dispatched
    deserves the better score even if the rules were confident about it.
    """
    if not settings.ai_engine_enabled:
        return 0

    from app.services.priority import build_queue

    with Session(engine) as session:
        candidates: list[uuid.UUID] = []

        for report in session.exec(
            select(Report).where(Report.scoring_provider != "local_granite")
        ).all():
            wanted, _ = needs_ai_rescore(report)
            if wanted:
                candidates.append(report.id)

        if settings.ai_escalate_top_n:
            for entry in build_queue(session)[: settings.ai_escalate_top_n]:
                if (
                    entry.report.scoring_provider != "local_granite"
                    and entry.report.id not in candidates
                ):
                    candidates.append(entry.report.id)

    if limit is not None:
        candidates = candidates[:limit]

    rescored = 0
    for report_id in candidates:
        if await rescore_with_ai(report_id):
            rescored += 1

    if candidates:
        logger.info(
            "AI re-score sweep complete",
            extra={"considered": len(candidates), "rescored": rescored},
        )
    return rescored


async def triage_pending(limit: int | None = None, *, triage_router: TriageRouter | None = None) -> int:
    """Score every report still awaiting a severity score.

    This is the retry queue FR-4 promises: anything ingestion could not score stays
    visible here and is picked up on the next sweep.
    """
    with Session(engine) as session:
        statement = select(Report.id).where(Report.severity_score.is_(None))  # type: ignore[union-attr]
        if limit is not None:
            statement = statement.limit(limit)
        pending = list(session.exec(statement).all())

    scored = 0
    for report_id in pending:
        if await triage_report(report_id, triage_router=triage_router) is not None:
            scored += 1

    if pending:
        logger.info("triage sweep complete", extra={"found": len(pending), "scored": scored})
    return scored
