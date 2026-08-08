"""Great-circle distance.

Built here in Phase 2 because the seed verifies its own corroboration and
impossible-movement fixtures with it. Phase 5 (500 m corroboration window) and Phase 7
(25 km dispatch radius) both consume it — one implementation, not three.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0

# Latitude/longitude bounds. A report outside these is not merely implausible, it is
# not a coordinate (FR-13).
MIN_LAT, MAX_LAT = -90.0, 90.0
MIN_LNG, MAX_LNG = -180.0, 180.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance between two points in metres."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lng2 - lng1)

    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return haversine_m(lat1, lng1, lat2, lng2) / 1000.0


def is_valid_coordinate(lat: float, lng: float) -> bool:
    """Range check only. Null island (0, 0) is a *valid* coordinate that is almost
    certainly wrong — authenticity scoring judges that separately (TRD §4.2)."""
    return MIN_LAT <= lat <= MAX_LAT and MIN_LNG <= lng <= MAX_LNG


def is_null_island(lat: float, lng: float, tolerance: float = 0.01) -> bool:
    """(0, 0) in the Gulf of Guinea — the classic 'GPS returned nothing' signature."""
    return abs(lat) < tolerance and abs(lng) < tolerance


def offset_metres(lat: float, lng: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Shift a coordinate by a local metre offset. Accurate enough at city scale."""
    new_lat = lat + north_m / 111_320.0
    new_lng = lng + east_m / (111_320.0 * cos(radians(lat)))
    return round(new_lat, 6), round(new_lng, 6)
