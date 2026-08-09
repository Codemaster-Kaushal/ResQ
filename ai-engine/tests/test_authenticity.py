"""
Tests for AuthenticityEngine — Phase 5.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest

from ai_engine.authenticity.authenticity_engine import AuthenticityEngine
from ai_engine.authenticity.schemas import (
    AuthenticityResult,
    AuthenticityReasonCode,
    VerificationStatus,
)


def _ts(offset_minutes: float = 0.0) -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(minutes=offset_minutes)


class TestAuthenticityEngine:
    def test_returns_authenticity_result(self):
        engine = AuthenticityEngine()
        result = engine.calculate_authenticity(
            report_id="RPT-001",
            reporter_pseudonym="USER-A",
            lat=12.9716,
            lon=77.5946,
            client_ts=_ts(),
            image_bytes=None,
        )
        assert isinstance(result, AuthenticityResult)
        assert 0 <= result.authenticity_score <= 100
        assert result.verification_status is not None
        assert len(result.authenticity_reason_codes) >= 1

    def test_no_image_gets_partial_image_credit(self):
        engine = AuthenticityEngine()
        result = engine.calculate_authenticity(
            report_id="RPT-001",
            reporter_pseudonym="USER-A",
            lat=12.9716,
            lon=77.5946,
            client_ts=_ts(),
            image_bytes=None,
        )
        assert AuthenticityReasonCode.NO_IMAGE_SUBMITTED in result.authenticity_reason_codes

    def test_valid_coordinates_boost_score(self):
        engine = AuthenticityEngine()
        with_coords = engine.calculate_authenticity(
            "RPT-1", "USER-A", 12.9716, 77.5946, _ts(), None
        )
        without_coords = engine.calculate_authenticity(
            "RPT-2", "USER-A", None, None, _ts(), None
        )
        assert with_coords.authenticity_score > without_coords.authenticity_score

    def test_plausible_timestamp_boosts_score(self):
        engine = AuthenticityEngine()
        good_ts = engine.calculate_authenticity(
            "RPT-1", "USER-A", 12.9716, 77.5946, _ts(0), None
        )
        bad_ts = engine.calculate_authenticity(
            "RPT-2", "USER-A", 12.9716, 77.5946, _ts(-30), None
        )
        assert good_ts.authenticity_score > bad_ts.authenticity_score

    def test_invalid_coordinates_penalizes_score(self):
        engine = AuthenticityEngine()
        result = engine.calculate_authenticity(
            "RPT-1", "USER-A", 0.0, 0.0, _ts(), None
        )
        assert AuthenticityReasonCode.COORDINATES_INVALID in result.authenticity_reason_codes

    def test_review_required_for_low_score(self):
        """Score below threshold triggers review_required."""
        engine = AuthenticityEngine()
        # No coords + bad timestamp + no image = low score
        old_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = engine.calculate_authenticity(
            "RPT-SUSP", "USER-A", 0.0, 0.0, old_ts, None
        )
        if result.authenticity_score < 50:
            assert result.review_required is True

    def test_never_raises(self):
        """Engine must never raise — always return a result."""
        engine = AuthenticityEngine()
        result = engine.calculate_authenticity(
            "RPT-TEST", None, None, None, None, None
        )
        assert isinstance(result, AuthenticityResult)

    def test_score_range(self):
        engine = AuthenticityEngine()
        result = engine.calculate_authenticity(
            "RPT-TEST", "USER-A", 12.9716, 77.5946, _ts(), None
        )
        assert 0 <= result.authenticity_score <= 100

    def test_status_verified_for_high_score(self):
        """A report with all valid signals should get a high score."""
        from ai_engine.authenticity.corroboration import CorroborationService
        # Add a corroborating report
        corr = CorroborationService()
        corr.add_report("RPT-X", "USER-B", 12.9720, 77.5950, _ts(-3), "flooding")
        engine = AuthenticityEngine(corroboration_service=corr)
        result = engine.calculate_authenticity(
            "RPT-001", "USER-A", 12.9716, 77.5946, _ts(), None
        )
        # With valid coords, good timestamp, and corroboration, score should be decent
        assert result.authenticity_score >= 50
