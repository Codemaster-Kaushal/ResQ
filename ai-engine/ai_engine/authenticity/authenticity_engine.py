"""
AuthenticityEngine — combines all trust signals into a single authenticity score.

Scoring is fully deterministic — no LLM black-box.
Weighted combination of:
    - Image originality (25 pts)
    - Geo validity      (20 pts)
    - Timestamp check   (20 pts)
    - Movement check    (15 pts)
    - Corroboration     (20 pts)
    Total max:          100 pts

Never deletes or rejects reports. Sets review_required=True for low scores.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ai_engine.config import (
    AUTHENTICITY_LIKELY_VALID_THRESHOLD,
    AUTHENTICITY_REVIEW_THRESHOLD,
    AUTHENTICITY_VERIFIED_THRESHOLD,
    CORROBORATION_RADIUS_METERS,
    CORROBORATION_TIME_WINDOW_MINUTES,
    GEO_VALIDITY_WEIGHT,
    IMAGE_ORIGINALITY_WEIGHT,
    MOVEMENT_PLAUSIBILITY_WEIGHT,
    TIME_PLAUSIBILITY_WEIGHT,
    CORROBORATION_WEIGHT,
)
from ai_engine.authenticity.corroboration import CorroborationService, NearbyReport
from ai_engine.authenticity.geo_check import validate_coordinates
from ai_engine.authenticity.image_duplicate import DuplicateCheckResult, ImageHashService
from ai_engine.authenticity.movement_check import MovementChecker, PreviousReport
from ai_engine.authenticity.schemas import (
    AuthenticityEvidence,
    AuthenticityReasonCode,
    AuthenticityResult,
    VerificationStatus,
)
from ai_engine.authenticity.time_check import check_timestamp

logger = logging.getLogger(__name__)


class AuthenticityEngine:
    """
    Combines all authenticity evidence into a single scored result.
    """

    def __init__(
        self,
        hash_service: Optional[ImageHashService] = None,
        corroboration_service: Optional[CorroborationService] = None,
        movement_checker: Optional[MovementChecker] = None,
    ) -> None:
        self._hash_service = hash_service or ImageHashService()
        self._corroboration_service = corroboration_service or CorroborationService()
        self._movement_checker = movement_checker or MovementChecker()

    def calculate_authenticity(
        self,
        report_id: str,
        reporter_pseudonym: Optional[str],
        lat: Optional[float],
        lon: Optional[float],
        client_ts: Optional[datetime],
        image_bytes: Optional[bytes],
        previous_reports: Optional[List[PreviousReport]] = None,
        known_hashes: Optional[Dict[str, str]] = None,
    ) -> AuthenticityResult:
        """
        Calculate authenticity score for a report.

        Args:
            report_id: Report identifier.
            reporter_pseudonym: Pseudonymous reporter ID.
            lat: Latitude (may be None).
            lon: Longitude (may be None).
            client_ts: Client-provided timestamp (may be None).
            image_bytes: Raw image bytes (may be None = no image submitted).
            previous_reports: Prior reports by this reporter for movement check.
            known_hashes: Known image hashes dict for duplicate check.

        Returns:
            AuthenticityResult with score, status, and evidence.
        """
        reason_codes: list[AuthenticityReasonCode] = []
        evidence = AuthenticityEvidence()
        score = 0.0
        server_ts = datetime.now(tz=timezone.utc)

        # ── 1. Image Originality (IMAGE_ORIGINALITY_WEIGHT pts) ───────────────
        if image_bytes is None:
            # No image submitted — neutral signal, grant partial credit
            score += IMAGE_ORIGINALITY_WEIGHT * 0.5
            reason_codes.append(AuthenticityReasonCode.NO_IMAGE_SUBMITTED)
            evidence.image_duplicate = False
        else:
            dup_result = self._hash_service.check_duplicate(
                image_bytes,
                known_hashes=known_hashes,
            )
            evidence.image_hash = dup_result.computed_hash or None

            if dup_result.is_duplicate:
                # Exact duplicate → 0 points
                reason_codes.append(AuthenticityReasonCode.IMAGE_DUPLICATE)
                evidence.image_duplicate = True
            elif dup_result.is_near_duplicate:
                # Near duplicate → 30% credit
                score += IMAGE_ORIGINALITY_WEIGHT * 0.3
                reason_codes.append(AuthenticityReasonCode.IMAGE_NEAR_DUPLICATE)
                evidence.image_duplicate = True
            else:
                # Original → full credit
                score += IMAGE_ORIGINALITY_WEIGHT
                reason_codes.append(AuthenticityReasonCode.IMAGE_NOT_DUPLICATE)
                evidence.image_duplicate = False

        # ── 2. Geo Validity (GEO_VALIDITY_WEIGHT pts) ─────────────────────────
        geo_result = validate_coordinates(lat, lon)
        reason_codes.append(geo_result.reason_code)
        evidence.geo_valid = geo_result.is_valid
        if geo_result.is_valid:
            score += GEO_VALIDITY_WEIGHT
        # Missing coordinates → 50% credit (common for low-tech reporters)
        elif geo_result.reason_code == AuthenticityReasonCode.COORDINATES_MISSING:
            score += GEO_VALIDITY_WEIGHT * 0.5

        # ── 3. Timestamp Plausibility (TIME_PLAUSIBILITY_WEIGHT pts) ──────────
        if client_ts is None:
            # No timestamp → neutral
            score += TIME_PLAUSIBILITY_WEIGHT * 0.5
            evidence.timestamp_plausible = True
        else:
            ts_result = check_timestamp(client_ts, server_ts)
            reason_codes.append(ts_result.reason_code)
            evidence.timestamp_plausible = ts_result.is_plausible
            if ts_result.is_plausible:
                score += TIME_PLAUSIBILITY_WEIGHT

        # ── 4. Movement Plausibility (MOVEMENT_PLAUSIBILITY_WEIGHT pts) ───────
        if lat is not None and lon is not None and client_ts is not None:
            move_result = self._movement_checker.check_movement(
                reporter_pseudonym=reporter_pseudonym or "anonymous",
                lat=lat,
                lon=lon,
                timestamp=client_ts,
                previous_reports=previous_reports or [],
            )
            reason_codes.append(move_result.reason_code)
            evidence.movement_plausible = move_result.is_plausible
            if move_result.is_plausible:
                score += MOVEMENT_PLAUSIBILITY_WEIGHT
        else:
            # Cannot check movement → neutral credit
            score += MOVEMENT_PLAUSIBILITY_WEIGHT * 0.5
            evidence.movement_plausible = True

        # ── 5. Corroboration (CORROBORATION_WEIGHT pts) ───────────────────────
        nearby: List[NearbyReport] = []
        if lat is not None and lon is not None and client_ts is not None:
            nearby = self._corroboration_service.find_nearby_reports(
                lat=lat,
                lon=lon,
                timestamp=client_ts,
                exclude_report_id=report_id,
                exclude_reporter=reporter_pseudonym,
            )
            independent_count = self._corroboration_service.count_independent_corroborators(
                nearby, current_reporter=reporter_pseudonym
            )
        else:
            independent_count = 0

        evidence.corroborating_reports = independent_count

        if independent_count >= 2:
            score += CORROBORATION_WEIGHT
            reason_codes.append(AuthenticityReasonCode.NEARBY_CORROBORATION)
        elif independent_count == 1:
            score += CORROBORATION_WEIGHT * 0.6
            reason_codes.append(AuthenticityReasonCode.NEARBY_CORROBORATION)
        else:
            reason_codes.append(AuthenticityReasonCode.NO_CORROBORATION)

        # ── Final score and status ─────────────────────────────────────────────
        final_score = int(round(max(0.0, min(100.0, score))))
        status = _status_from_score(final_score)
        review_required = final_score < AUTHENTICITY_REVIEW_THRESHOLD

        logger.info(
            "Authenticity: report=%s score=%d status=%s review=%s codes=%s",
            report_id, final_score, status, review_required,
            [c.value for c in reason_codes],
        )

        return AuthenticityResult(
            authenticity_score=final_score,
            verification_status=status,
            review_required=review_required,
            authenticity_reason_codes=reason_codes,
            evidence=evidence,
        )


def _status_from_score(score: int) -> VerificationStatus:
    """Map numeric score to VerificationStatus."""
    if score >= AUTHENTICITY_VERIFIED_THRESHOLD:
        return VerificationStatus.VERIFIED
    if score >= AUTHENTICITY_LIKELY_VALID_THRESHOLD:
        return VerificationStatus.LIKELY_VALID
    if score >= AUTHENTICITY_REVIEW_THRESHOLD:
        return VerificationStatus.NEEDS_REVIEW
    return VerificationStatus.FLAGGED
