"""
Geo-coordinate validation for Phase 5 (FR-13).
Validates coordinates are within valid ranges and computes distances.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional

from ai_engine.authenticity.schemas import AuthenticityReasonCode

logger = logging.getLogger(__name__)

# Earth radius in meters
_EARTH_RADIUS_M = 6_371_000.0


@dataclass
class GeoCheckResult:
    """Result of coordinate validation."""
    is_valid: bool
    reason_code: AuthenticityReasonCode
    details: str


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Compute the great-circle distance between two coordinates in meters.

    Args:
        lat1, lon1: First coordinate (degrees).
        lat2, lon2: Second coordinate (degrees).

    Returns:
        Distance in meters.
    """
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_M * c


def validate_coordinates(
    lat: Optional[float],
    lon: Optional[float],
) -> GeoCheckResult:
    """
    Validate that coordinates are within legal geographic bounds.

    Args:
        lat: Latitude (must be -90 to 90).
        lon: Longitude (must be -180 to 180).

    Returns:
        GeoCheckResult.
    """
    if lat is None or lon is None:
        return GeoCheckResult(
            is_valid=False,
            reason_code=AuthenticityReasonCode.COORDINATES_MISSING,
            details="Latitude or longitude not provided.",
        )

    if not (-90.0 <= lat <= 90.0):
        return GeoCheckResult(
            is_valid=False,
            reason_code=AuthenticityReasonCode.COORDINATES_INVALID,
            details=f"Latitude {lat} is out of range [-90, 90].",
        )

    if not (-180.0 <= lon <= 180.0):
        return GeoCheckResult(
            is_valid=False,
            reason_code=AuthenticityReasonCode.COORDINATES_INVALID,
            details=f"Longitude {lon} is out of range [-180, 180].",
        )

    # (0, 0) is a sentinel that almost certainly means no GPS fix
    if lat == 0.0 and lon == 0.0:
        return GeoCheckResult(
            is_valid=False,
            reason_code=AuthenticityReasonCode.COORDINATES_INVALID,
            details="Coordinates (0, 0) are likely a missing-GPS sentinel.",
        )

    return GeoCheckResult(
        is_valid=True,
        reason_code=AuthenticityReasonCode.COORDINATES_VALID,
        details=f"Coordinates ({lat}, {lon}) are within valid range.",
    )
