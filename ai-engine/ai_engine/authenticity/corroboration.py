"""
Corroboration service for Phase 5 (FR-14).
Finds independent reports near a given location and time window.
In-memory store — interface designed for DB-backed injection by Person 2.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from ai_engine.config import (
    CORROBORATION_RADIUS_METERS,
    CORROBORATION_TIME_WINDOW_MINUTES,
)
from ai_engine.authenticity.geo_check import haversine_distance

logger = logging.getLogger(__name__)


@dataclass
class NearbyReport:
    """A report found near the current one."""
    report_id: str
    reporter_pseudonym: str
    lat: float
    lon: float
    timestamp: datetime
    incident_type: str
    distance_meters: float


@dataclass
class StoredReport:
    """Internal stored report for corroboration lookups."""
    report_id: str
    reporter_pseudonym: str
    lat: float
    lon: float
    timestamp: datetime
    incident_type: str


class CorroborationService:
    """
    Finds independent corroborating reports near a given location and time.
    Uses an in-memory store suitable for testing and demo.
    Person 2 can subclass and override `find_nearby_reports` to use a DB.
    """

    def __init__(
        self,
        radius_meters: int = CORROBORATION_RADIUS_METERS,
        time_window_minutes: int = CORROBORATION_TIME_WINDOW_MINUTES,
    ) -> None:
        self._radius_meters = radius_meters
        self._time_window_minutes = time_window_minutes
        self._reports: list[StoredReport] = []

    def add_report(
        self,
        report_id: str,
        reporter_pseudonym: str,
        lat: float,
        lon: float,
        timestamp: datetime,
        incident_type: str,
    ) -> None:
        """
        Register a report in the in-memory store.

        Args:
            report_id: Unique report identifier.
            reporter_pseudonym: Pseudonymous reporter ID.
            lat: Latitude.
            lon: Longitude.
            timestamp: Report timestamp.
            incident_type: Incident type string.
        """
        self._reports.append(
            StoredReport(
                report_id=report_id,
                reporter_pseudonym=reporter_pseudonym,
                lat=lat,
                lon=lon,
                timestamp=timestamp,
                incident_type=incident_type,
            )
        )
        logger.debug("Corroboration store: added report %s", report_id)

    def find_nearby_reports(
        self,
        lat: float,
        lon: float,
        timestamp: datetime,
        distance_meters: Optional[int] = None,
        time_window_minutes: Optional[int] = None,
        exclude_report_id: Optional[str] = None,
        exclude_reporter: Optional[str] = None,
    ) -> List[NearbyReport]:
        """
        Find reports within the given radius and time window.

        Args:
            lat: Center latitude.
            lon: Center longitude.
            timestamp: Reference time.
            distance_meters: Search radius override (uses config default if None).
            time_window_minutes: Time window override (uses config default if None).
            exclude_report_id: Exclude this report from results (the current one).
            exclude_reporter: Exclude this reporter to ensure independence.

        Returns:
            List of NearbyReport sorted by distance.
        """
        radius = distance_meters if distance_meters is not None else self._radius_meters
        window = time_window_minutes if time_window_minutes is not None else self._time_window_minutes

        def _to_utc(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        ref_utc = _to_utc(timestamp)
        nearby: list[NearbyReport] = []

        for stored in self._reports:
            if exclude_report_id and stored.report_id == exclude_report_id:
                continue
            if exclude_reporter and stored.reporter_pseudonym == exclude_reporter:
                continue

            dist = haversine_distance(lat, lon, stored.lat, stored.lon)
            if dist > radius:
                continue

            stored_utc = _to_utc(stored.timestamp)
            time_diff_minutes = abs((ref_utc - stored_utc).total_seconds()) / 60.0
            if time_diff_minutes > window:
                continue

            nearby.append(
                NearbyReport(
                    report_id=stored.report_id,
                    reporter_pseudonym=stored.reporter_pseudonym,
                    lat=stored.lat,
                    lon=stored.lon,
                    timestamp=stored.timestamp,
                    incident_type=stored.incident_type,
                    distance_meters=round(dist, 1),
                )
            )

        nearby.sort(key=lambda r: r.distance_meters)
        return nearby

    def count_independent_corroborators(
        self,
        nearby_reports: List[NearbyReport],
        current_reporter: Optional[str],
    ) -> int:
        """
        Count the number of unique reporters (excluding the current one) in nearby_reports.

        Args:
            nearby_reports: Reports found by find_nearby_reports.
            current_reporter: The reporter pseudonym to exclude.

        Returns:
            Count of independent unique corroborators.
        """
        reporters = {
            r.reporter_pseudonym
            for r in nearby_reports
            if r.reporter_pseudonym != current_reporter
        }
        return len(reporters)
