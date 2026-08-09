"""
Timestamp plausibility check for Phase 5 (FR-13).
Detects suspicious clock skew between client and server timestamps.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from ai_engine.config import MAX_CLOCK_SKEW_MINUTES
from ai_engine.authenticity.schemas import AuthenticityReasonCode

logger = logging.getLogger(__name__)


@dataclass
class TimeCheckResult:
    """Result of a timestamp plausibility check."""
    is_plausible: bool
    skew_minutes: float
    reason_code: AuthenticityReasonCode


def check_timestamp(
    client_ts: datetime,
    server_ts: datetime,
    max_skew_minutes: int = MAX_CLOCK_SKEW_MINUTES,
) -> TimeCheckResult:
    """
    Check whether the client timestamp is plausible compared to the server time.

    Args:
        client_ts: Timestamp reported by the client (may be naive or aware).
        server_ts: Server-side timestamp (may be naive or aware).
        max_skew_minutes: Maximum acceptable difference in minutes.

    Returns:
        TimeCheckResult.
    """
    # Normalize to UTC-aware datetimes
    def _to_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    client_utc = _to_utc(client_ts)
    server_utc = _to_utc(server_ts)

    skew_seconds = abs((client_utc - server_utc).total_seconds())
    skew_minutes = skew_seconds / 60.0

    is_plausible = skew_minutes <= max_skew_minutes

    logger.debug(
        "Timestamp check: client=%s server=%s skew=%.1fmin plausible=%s",
        client_utc, server_utc, skew_minutes, is_plausible,
    )

    return TimeCheckResult(
        is_plausible=is_plausible,
        skew_minutes=round(skew_minutes, 2),
        reason_code=(
            AuthenticityReasonCode.TIMESTAMP_PLAUSIBLE
            if is_plausible
            else AuthenticityReasonCode.TIMESTAMP_SKEW
        ),
    )
