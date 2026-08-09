"""
Tests for timestamp plausibility check — Phase 5.
"""

from datetime import datetime, timezone, timedelta

import pytest

from ai_engine.authenticity.time_check import TimeCheckResult, check_timestamp
from ai_engine.authenticity.schemas import AuthenticityReasonCode


def _ts(offset_minutes: float = 0.0) -> datetime:
    """Return a UTC datetime offset by given minutes from now."""
    return datetime.now(tz=timezone.utc) + timedelta(minutes=offset_minutes)


class TestCheckTimestamp:
    def test_same_timestamp_is_plausible(self):
        now = _ts()
        result = check_timestamp(now, now)
        assert result.is_plausible is True
        assert result.skew_minutes == pytest.approx(0.0, abs=0.1)
        assert result.reason_code == AuthenticityReasonCode.TIMESTAMP_PLAUSIBLE

    def test_small_skew_is_plausible(self):
        client = _ts(-5)
        server = _ts(0)
        result = check_timestamp(client, server, max_skew_minutes=15)
        assert result.is_plausible is True
        assert result.skew_minutes == pytest.approx(5.0, abs=0.1)

    def test_large_skew_is_implausible(self):
        client = _ts(-30)
        server = _ts(0)
        result = check_timestamp(client, server, max_skew_minutes=15)
        assert result.is_plausible is False
        assert result.reason_code == AuthenticityReasonCode.TIMESTAMP_SKEW

    def test_future_timestamp_is_implausible(self):
        client = _ts(30)
        server = _ts(0)
        result = check_timestamp(client, server, max_skew_minutes=15)
        assert result.is_plausible is False

    def test_exactly_at_threshold_is_plausible(self):
        client = _ts(-15)
        server = _ts(0)
        result = check_timestamp(client, server, max_skew_minutes=15)
        assert result.is_plausible is True

    def test_naive_datetimes_treated_as_utc(self):
        client = datetime(2026, 1, 1, 12, 0, 0)  # naive
        server = datetime(2026, 1, 1, 12, 5, 0)  # naive
        result = check_timestamp(client, server)
        assert result.is_plausible is True
        assert result.skew_minutes == pytest.approx(5.0, abs=0.1)

    def test_historic_timestamp_large_skew(self):
        """Report filed years in the past = very large skew."""
        client = datetime(2020, 1, 1, tzinfo=timezone.utc)
        server = datetime(2026, 8, 8, tzinfo=timezone.utc)
        result = check_timestamp(client, server)
        assert result.is_plausible is False
        assert result.skew_minutes > 15
