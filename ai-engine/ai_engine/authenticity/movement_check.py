"""
Movement plausibility check for Phase 5 (FR-13).
Detects impossible reporter movement between consecutive reports.
Does NOT reject reports — only flags for review.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from ai_engine.config import MAX_PLAUSIBLE_SPEED_KMH
from ai_engine.authenticity.geo_check import haversine_distance
from ai_engine.authenticity.schemas import AuthenticityReasonCode

logger = logging.getLogger(__name__)


@dataclass
class MovementCheckResult:
    """Result of a movement plausibility check."""
    is_plausible: bool
    required_speed_kmh: Optional[float]
    reason_code: AuthenticityReasonCode


@dataclass
class PreviousReport:
    """Minimal representation of a prior report for movement checking."""
    report_id: str
    lat: float
    lon: float
    timestamp: datetime


class MovementChecker:
    """
    Checks if a reporter's movement between consecutive reports is physically plausible.
    Flags impossible movement (speed > MAX_PLAUSIBLE_SPEED_KMH) for review.
    Never deletes or rejects reports.
    """

    def __init__(self, max_speed_kmh: float = MAX_PLAUSIBLE_SPEED_KMH) -> None:
        self._max_speed = max_speed_kmh

    def check_movement(
        self,
        reporter_pseudonym: str,
        lat: Optional[float],
        lon: Optional[float],
        timestamp: datetime,
        previous_reports: List[PreviousReport],
    ) -> MovementCheckResult:
        """
        Check if movement from previous report locations to (lat, lon) is plausible.

        Args:
            reporter_pseudonym: Pseudonymous reporter ID.
            lat: Current latitude.
            lon: Current longitude.
            timestamp: Current report timestamp.
            previous_reports: List of prior reports by the same reporter.

        Returns:
            MovementCheckResult — is_plausible=True if no impossible movement detected.
        """
        if lat is None or lon is None:
            # No coordinates → cannot check movement, assume plausible
            return MovementCheckResult(
                is_plausible=True,
                required_speed_kmh=None,
                reason_code=AuthenticityReasonCode.MOVEMENT_PLAUSIBLE,
            )

        if not previous_reports:
            return MovementCheckResult(
                is_plausible=True,
                required_speed_kmh=None,
                reason_code=AuthenticityReasonCode.MOVEMENT_PLAUSIBLE,
            )

        # Normalize timestamp
        def _to_utc(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        current_utc = _to_utc(timestamp)

        max_required_speed = 0.0

        for prev in previous_reports:
            prev_utc = _to_utc(prev.timestamp)
            time_diff_seconds = (current_utc - prev_utc).total_seconds()

            # If timestamps are in the future relative to previous, skip
            if time_diff_seconds <= 0:
                continue

            dist_m = haversine_distance(prev.lat, prev.lon, lat, lon)
            dist_km = dist_m / 1000.0
            time_hours = time_diff_seconds / 3600.0

            if time_hours == 0:
                continue

            required_speed = dist_km / time_hours
            if required_speed > max_required_speed:
                max_required_speed = required_speed

        if max_required_speed > self._max_speed:
            logger.warning(
                "Impossible movement for %s: required_speed=%.1f km/h (max=%.1f)",
                reporter_pseudonym, max_required_speed, self._max_speed,
            )
            return MovementCheckResult(
                is_plausible=False,
                required_speed_kmh=round(max_required_speed, 2),
                reason_code=AuthenticityReasonCode.IMPOSSIBLE_MOVEMENT,
            )

        return MovementCheckResult(
            is_plausible=True,
            required_speed_kmh=round(max_required_speed, 2) if max_required_speed > 0 else None,
            reason_code=AuthenticityReasonCode.MOVEMENT_PLAUSIBLE,
        )
