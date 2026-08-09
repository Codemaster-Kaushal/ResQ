"""Authenticity and trust scoring (TRD §4.2).

Every report starts at a baseline and is adjusted by evidence:

| Signal              | Adjustment |
|---------------------|------------|
| DUPLICATE_IMAGE     | −45 |
| IMPOSSIBLE_MOVEMENT | −30 |
| GEO_IMPLAUSIBLE     | −25 |
| STALE_REPORT        | −15 |
| LOW_INFORMATION     | −10 |
| CORROBORATED        | +25 |
| EXIF_CONSISTENT     | +10 |

Below the flag threshold a report becomes ``flagged`` and goes to human review.

**Nothing here rejects, deletes, or hides a report** (FR-15, TRD §10). A low score is a
routing decision, not a verdict: rejection is a human action taken through
``POST /api/reports/{id}/review`` and is recorded as such. During a mass-casualty event
the cost of silently discarding one true report dwarfs the cost of reviewing a false one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import imagehash
from sqlmodel import Session, select

from app.ai.local import classify_incident
from app.config import settings
from app.core.geo import haversine_km, haversine_m, is_null_island, is_valid_coordinate
from app.core.logging import get_logger
from app.core.time import minutes_between
from app.db import engine
from app.models import Activity, Report, ReportStatus
from app.services.events import emit_event
from app.services.media import read_exif_from_path
from app.services.triage import reason

logger = get_logger(__name__)

# Adjustments from TRD §4.2. Thresholds are env-driven; these weights are the
# specification itself, so they live in code where they can be read alongside it.
DUPLICATE_IMAGE_PENALTY = -45
IMPOSSIBLE_MOVEMENT_PENALTY = -30
GEO_IMPLAUSIBLE_PENALTY = -25
STALE_REPORT_PENALTY = -15
LOW_INFORMATION_PENALTY = -10
CORROBORATED_BONUS = 25
EXIF_CONSISTENT_BONUS = 10

AUTHENTICITY_MIN, AUTHENTICITY_MAX = 0, 100


@dataclass
class Authenticity:
    score: int
    reasons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reason_codes(self) -> list[str]:
        return [item["code"] for item in self.reasons]


def _hamming(left: str, right: str) -> int | None:
    try:
        return imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right)
    except (ValueError, TypeError):
        return None


def _earlier_reports(session: Session, report: Report) -> list[Report]:
    """Reports already on file when this one arrived.

    Ordering by receipt matters: the report that *re-uses* an image is the suspicious
    one, and the original must not be punished for being first. Ties break on id so
    that two reports sharing a receipt timestamp cannot each count the other as
    earlier and both end up penalised.
    """
    everything = session.exec(select(Report).where(Report.id != report.id)).all()
    mine = (report.received_at, str(report.id))
    return [other for other in everything if (other.received_at, str(other.id)) < mine]


def find_duplicate_image(
    session: Session, report: Report, candidates: list[Report] | None = None
) -> tuple[Report, int] | None:
    """Nearest earlier report whose image is a perceptual near-duplicate."""
    if not report.image_phash:
        return None

    pool = candidates if candidates is not None else _earlier_reports(session, report)
    best: tuple[Report, int] | None = None

    for other in pool:
        if not other.image_phash:
            continue
        distance = _hamming(report.image_phash, other.image_phash)
        if distance is None or distance > settings.phash_duplicate_distance:
            continue
        if best is None or distance < best[1]:
            best = (other, distance)

    return best


def _shares_image_with(report: Report, other: Report) -> bool:
    """True when two reports carry the same photograph.

    Reports built on one recycled image are not independent observations — treating
    them as corroboration is exactly the false-amplification this stage exists to stop.
    """
    if not report.image_phash or not other.image_phash:
        return False
    distance = _hamming(report.image_phash, other.image_phash)
    return distance is not None and distance <= settings.phash_duplicate_distance


def find_corroborating(
    session: Session, report: Report, candidates: list[Report] | None = None
) -> list[Report]:
    """Independent reports of the same event, near in space and time (FR-14).

    "Independent" is enforced three ways: a different reporter, a different
    photograph, and — because FR-14 says *the same event* rather than merely
    "nearby" — the same incident type. Two unrelated incidents on one street corner
    do not make either of them more credible.
    """
    pool = (
        candidates
        if candidates is not None
        else list(session.exec(select(Report).where(Report.id != report.id)).all())
    )

    window = timedelta(minutes=settings.corroboration_window_min)
    matches: list[Report] = []

    for other in pool:
        if other.id == report.id:
            continue
        if other.reporter_pseudonym == report.reporter_pseudonym:
            continue
        if abs(other.client_created_at - report.client_created_at) > window:
            continue
        if haversine_m(report.lat, report.lng, other.lat, other.lng) > settings.corroboration_radius_m:
            continue
        if settings.corroboration_require_same_type and other.incident_type != report.incident_type:
            continue
        if _shares_image_with(report, other):
            continue
        matches.append(other)

    return matches


def find_impossible_movement(
    session: Session, report: Report, candidates: list[Report] | None = None
) -> tuple[Report, float] | None:
    """The same pseudonym reporting from implausibly far away, implausibly soon."""
    pool = (
        candidates
        if candidates is not None
        else list(
            session.exec(
                select(Report).where(
                    Report.id != report.id,
                    Report.reporter_pseudonym == report.reporter_pseudonym,
                )
            ).all()
        )
    )

    for other in pool:
        if other.id == report.id or other.reporter_pseudonym != report.reporter_pseudonym:
            continue
        gap = abs(minutes_between(report.client_created_at, other.client_created_at))
        if gap > settings.impossible_movement_window_min:
            continue
        distance = haversine_km(report.lat, report.lng, other.lat, other.lng)
        if distance > settings.impossible_movement_km:
            return other, distance

    return None


def is_low_information(text: str) -> bool:
    """Too short to act on and naming no recognisable incident."""
    tokens = (text or "").split()
    if len(tokens) >= settings.low_information_max_tokens:
        return False
    _, evidence = classify_incident((text or "").lower())
    return evidence == 0


def exif_distance_km(report: Report) -> float | None:
    """Kilometres between the photograph's own GPS and the reported location."""
    if not report.image_path:
        return None

    snapshot = read_exif_from_path(settings.media_dir / report.image_path)
    if not snapshot.has_gps:
        return None

    return haversine_km(report.lat, report.lng, snapshot.lat, snapshot.lng)  # type: ignore[arg-type]


def compute_authenticity_via_engine(session: Session, report: Report) -> Authenticity | None:
    """Delegate the trust score to the AI engine, fed from our own database.

    The engine's trust stage is deterministic and uses no LLM, so unlike
    severity it costs nothing and can run inline on every report.

    What it must *not* use is its own `state.json`: that file only knows the
    reports that passed through the engine, so it would miss the seeded
    duplicate pair and find no corroboration. `calculate_authenticity()` takes
    prior reports and known hashes as arguments, so it gets the real table
    instead and the database stays the single source of truth.

    Returns None when the engine is unavailable, so the caller falls back to
    the backend's own §4.2 implementation.
    """
    if not settings.ai_engine_enabled:
        return None

    from app.ai.resq_engine import engine_provider
    from app.services import ai_state

    if not engine_provider.is_available():
        return None

    try:
        verdict = engine_provider.assess(
            report_id=str(report.id),
            pseudonym=report.reporter_pseudonym,
            lat=report.lat,
            lng=report.lng,
            client_timestamp=report.client_created_at,
            image_bytes=ai_state.image_bytes_for(report),
            previous_reports=ai_state.previous_reports_for(session, report),
            known_hashes=ai_state.known_hashes_for(session, report),
        )
    except Exception:
        logger.exception(
            "AI authenticity failed; falling back to the built-in trust engine",
            extra={"report_id": str(report.id)},
        )
        return None

    reasons = [*verdict.reasons, reason(f"BAND_{verdict.band}", 0, "local_granite")]
    return Authenticity(score=verdict.score, reasons=reasons)


def compute_authenticity(session: Session, report: Report) -> Authenticity:
    """Apply TRD §4.2 to one report. Read-only: it decides nothing on its own."""
    reasons: list[dict[str, Any]] = [
        reason("BASELINE", settings.authenticity_baseline, "system")
    ]
    total = settings.authenticity_baseline

    earlier = _earlier_reports(session, report)

    duplicate = find_duplicate_image(session, report, earlier)
    if duplicate is not None:
        other, distance = duplicate
        total += DUPLICATE_IMAGE_PENALTY
        reasons.append(reason("DUPLICATE_IMAGE", DUPLICATE_IMAGE_PENALTY, "image"))
        logger.info(
            "duplicate image detected",
            extra={
                "report_id": str(report.id),
                "matches": str(other.id),
                "hamming_distance": distance,
            },
        )

    movement = find_impossible_movement(session, report)
    if movement is not None:
        total += IMPOSSIBLE_MOVEMENT_PENALTY
        reasons.append(reason("IMPOSSIBLE_MOVEMENT", IMPOSSIBLE_MOVEMENT_PENALTY, "geo"))

    if not is_valid_coordinate(report.lat, report.lng) or is_null_island(report.lat, report.lng):
        total += GEO_IMPLAUSIBLE_PENALTY
        reasons.append(reason("GEO_IMPLAUSIBLE", GEO_IMPLAUSIBLE_PENALTY, "geo"))

    stale_after = timedelta(hours=settings.stale_report_hours)
    if report.received_at - report.client_created_at > stale_after:
        total += STALE_REPORT_PENALTY
        reasons.append(reason("STALE_REPORT", STALE_REPORT_PENALTY, "time"))

    if is_low_information(report.text):
        total += LOW_INFORMATION_PENALTY
        reasons.append(reason("LOW_INFORMATION", LOW_INFORMATION_PENALTY, "text"))

    corroborating = find_corroborating(session, report)
    # The cluster counts this report too, so N others means N+1 reports of the event.
    if len(corroborating) + 1 >= settings.corroboration_min_reports:
        total += CORROBORATED_BONUS
        reasons.append(reason("CORROBORATED", CORROBORATED_BONUS, "cluster"))

    distance_km = exif_distance_km(report)
    if distance_km is not None and distance_km <= settings.exif_match_radius_km:
        total += EXIF_CONSISTENT_BONUS
        reasons.append(reason("EXIF_CONSISTENT", EXIF_CONSISTENT_BONUS, "image"))

    score = max(AUTHENTICITY_MIN, min(AUTHENTICITY_MAX, total))
    if score != total:
        adjustment = score - total
        code = "SCORE_CLAMPED_AT_MAX" if adjustment < 0 else "SCORE_CLAMPED_AT_MIN"
        reasons.append(reason(code, adjustment, "system"))

    return Authenticity(score=score, reasons=reasons)


def apply_authenticity(report: Report, assessment: Authenticity) -> ReportStatus:
    """Write the assessment onto the report and route it. Does not commit.

    The only two outcomes are ``verified`` and ``flagged``. ``rejected`` is
    unreachable from here by design — no automated path may reject a report.
    """
    report.authenticity_score = assessment.score
    report.authenticity_reasons = assessment.reasons

    if report.status == ReportStatus.CLASSIFIED:
        report.status = (
            ReportStatus.FLAGGED
            if assessment.score < settings.authenticity_flag_threshold
            else ReportStatus.VERIFIED
        )

    return report.status


async def assess_report(report_id: uuid.UUID, *, force: bool = False) -> Authenticity | None:
    """Score one report's authenticity. Never raises."""
    try:
        with Session(engine) as session:
            report = session.get(Report, report_id)
            if report is None:
                logger.warning(
                    "authenticity skipped: report not found", extra={"report_id": str(report_id)}
                )
                return None

            if report.authenticity_score is not None and not force:
                return None

            assessment = compute_authenticity_via_engine(session, report)
            scored_by = "local_granite"
            if assessment is None:
                assessment = compute_authenticity(session, report)
                scored_by = "local"
            status = apply_authenticity(report, assessment)

            session.add(report)
            emit_event(
                session,
                case_id=report.id,
                activity=Activity.AUTHENTICITY_SCORED,
                metadata={
                    "authenticity_score": assessment.score,
                    "reason_codes": assessment.reason_codes,
                    "threshold": settings.authenticity_flag_threshold,
                    "scored_by": scored_by,
                },
            )
            # The routing decision is its own event: it is what a human acts on, and
            # Phase 9's mining measures how long a flagged report waits for review.
            if status in (ReportStatus.FLAGGED, ReportStatus.VERIFIED):
                emit_event(
                    session,
                    case_id=report.id,
                    activity=(
                        Activity.REPORT_FLAGGED
                        if status == ReportStatus.FLAGGED
                        else Activity.REPORT_VERIFIED
                    ),
                    metadata={"authenticity_score": assessment.score},
                )
            session.commit()

        logger.info(
            "report authenticity assessed",
            extra={
                "report_id": str(report_id),
                "authenticity_score": assessment.score,
                "status": status.value,
                "reason_codes": assessment.reason_codes,
            },
        )
        return assessment

    except Exception:  # noqa: BLE001 — a background task must never take the app down
        logger.exception(
            "authenticity scoring failed; report left for retry",
            extra={"report_id": str(report_id)},
        )
        return None


async def assess_pending(limit: int | None = None) -> int:
    """Assess every classified report still without an authenticity score."""
    with Session(engine) as session:
        statement = (
            select(Report.id)
            .where(Report.authenticity_score.is_(None))  # type: ignore[union-attr]
            .where(Report.severity_score.is_not(None))  # type: ignore[union-attr]
            .order_by(Report.received_at, Report.id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        pending = list(session.exec(statement).all())

    assessed = 0
    for report_id in pending:
        if await assess_report(report_id) is not None:
            assessed += 1

    if pending:
        logger.info(
            "authenticity sweep complete", extra={"found": len(pending), "assessed": assessed}
        )
    return assessed
