"""
Tests for geo-coordinate validation — Phase 5.
"""

import pytest

from ai_engine.authenticity.geo_check import GeoCheckResult, haversine_distance, validate_coordinates
from ai_engine.authenticity.schemas import AuthenticityReasonCode


class TestHaversineDistance:
    def test_same_point_is_zero(self):
        dist = haversine_distance(12.9716, 77.5946, 12.9716, 77.5946)
        assert dist == pytest.approx(0.0, abs=0.01)

    def test_known_distance(self):
        # Distance between Bangalore and Delhi ≈ 1750 km
        dist = haversine_distance(12.9716, 77.5946, 28.6139, 77.2090)
        assert 1_700_000 < dist < 1_800_000

    def test_small_distance(self):
        # Two points ~100m apart
        dist = haversine_distance(12.9716, 77.5946, 12.9725, 77.5946)
        assert 50 < dist < 200

    def test_antipodal_points(self):
        dist = haversine_distance(0, 0, 0, 180)
        assert dist == pytest.approx(20_015_086, rel=0.01)


class TestValidateCoordinates:
    def test_valid_coordinates(self):
        result = validate_coordinates(12.9716, 77.5946)
        assert result.is_valid is True
        assert result.reason_code == AuthenticityReasonCode.COORDINATES_VALID

    def test_none_latitude(self):
        result = validate_coordinates(None, 77.5946)
        assert result.is_valid is False
        assert result.reason_code == AuthenticityReasonCode.COORDINATES_MISSING

    def test_none_longitude(self):
        result = validate_coordinates(12.9716, None)
        assert result.is_valid is False
        assert result.reason_code == AuthenticityReasonCode.COORDINATES_MISSING

    def test_both_none(self):
        result = validate_coordinates(None, None)
        assert result.is_valid is False

    def test_out_of_range_latitude(self):
        result = validate_coordinates(91.0, 77.0)
        assert result.is_valid is False
        assert result.reason_code == AuthenticityReasonCode.COORDINATES_INVALID

    def test_out_of_range_longitude(self):
        result = validate_coordinates(12.9, 181.0)
        assert result.is_valid is False
        assert result.reason_code == AuthenticityReasonCode.COORDINATES_INVALID

    def test_zero_zero_is_invalid(self):
        """(0, 0) is treated as missing GPS fix."""
        result = validate_coordinates(0.0, 0.0)
        assert result.is_valid is False
        assert result.reason_code == AuthenticityReasonCode.COORDINATES_INVALID

    def test_negative_coordinates_valid(self):
        result = validate_coordinates(-33.8688, 151.2093)  # Sydney
        assert result.is_valid is True

    def test_boundary_coordinates_valid(self):
        result = validate_coordinates(90.0, 180.0)
        assert result.is_valid is True
