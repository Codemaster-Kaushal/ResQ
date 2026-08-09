"""
Tests for movement plausibility check — Phase 5.
"""

from datetime import datetime, timezone, timedelta

import pytest

from ai_engine.authenticity.movement_check import MovementChecker, MovementCheckResult, PreviousReport
from ai_engine.authenticity.schemas import AuthenticityReasonCode


def _ts(offset_hours: float = 0.0) -> datetime:
    return datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc) + timedelta(hours=offset_hours)


class TestMovementChecker:
    def test_no_previous_reports_always_plausible(self):
        checker = MovementChecker()
        result = checker.check_movement(
            "USER-A", 12.9716, 77.5946, _ts(), []
        )
        assert result.is_plausible is True
        assert result.reason_code == AuthenticityReasonCode.MOVEMENT_PLAUSIBLE

    def test_none_coordinates_always_plausible(self):
        checker = MovementChecker()
        result = checker.check_movement("USER-A", None, None, _ts(), [])
        assert result.is_plausible is True

    def test_normal_movement_is_plausible(self):
        checker = MovementChecker(max_speed_kmh=300)
        prev = PreviousReport("RPT-OLD", 12.9716, 77.5946, _ts(-1.0))  # 1 hour ago
        # Current location ~5km away, 1hr later → 5 km/h (well under 300)
        result = checker.check_movement(
            "USER-A", 12.9760, 77.5960, _ts(), [prev]
        )
        assert result.is_plausible is True

    def test_impossible_movement_flagged(self):
        checker = MovementChecker(max_speed_kmh=300)
        prev = PreviousReport("RPT-OLD", 12.9716, 77.5946, _ts(-0.001))  # 3.6 seconds ago
        # Current location ~2000km away in 3.6s → impossible
        result = checker.check_movement(
            "USER-A", 28.6139, 77.2090, _ts(), [prev]
        )
        assert result.is_plausible is False
        assert result.reason_code == AuthenticityReasonCode.IMPOSSIBLE_MOVEMENT
        assert result.required_speed_kmh is not None
        assert result.required_speed_kmh > 300

    def test_same_location_is_plausible(self):
        checker = MovementChecker()
        prev = PreviousReport("RPT-OLD", 12.9716, 77.5946, _ts(-0.5))
        result = checker.check_movement(
            "USER-A", 12.9716, 77.5946, _ts(), [prev]
        )
        assert result.is_plausible is True

    def test_multiple_previous_reports_uses_worst_case(self):
        checker = MovementChecker(max_speed_kmh=300)
        prev_ok = PreviousReport("RPT-OK", 12.9716, 77.5946, _ts(-2.0))
        prev_bad = PreviousReport("RPT-BAD", 28.6139, 77.2090, _ts(-0.001))
        result = checker.check_movement(
            "USER-A", 12.9716, 77.5946, _ts(),
            [prev_ok, prev_bad]
        )
        # The bad one should trigger the flag
        assert result.is_plausible is False

    def test_never_deletes_or_raises(self):
        """Movement check must never raise — only return a result."""
        checker = MovementChecker()
        result = checker.check_movement(
            "USER-A", 0.0, 0.0, _ts(), []
        )
        assert isinstance(result, MovementCheckResult)
