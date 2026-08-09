"""Process-level facts, shared by /health and /api/governance."""

from __future__ import annotations

import time

_STARTED_AT = time.monotonic()


def uptime_seconds() -> float:
    """Seconds since this process started. Monotonic, so a clock change cannot skew it."""
    return round(time.monotonic() - _STARTED_AT, 3)
