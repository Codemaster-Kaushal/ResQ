"""
Tests for CorroborationService — Phase 5.
"""

from datetime import datetime, timezone, timedelta

import pytest

from ai_engine.authenticity.corroboration import CorroborationService, NearbyReport


def _ts(offset_minutes: float = 0.0) -> datetime:
    return datetime(2026, 8, 8, 18, 30, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes)


class TestCorroborationService:
    def test_no_reports_returns_empty(self):
        svc = CorroborationService()
        result = svc.find_nearby_reports(12.9716, 77.5946, _ts())
        assert result == []

    def test_nearby_report_found(self):
        svc = CorroborationService(radius_meters=500, time_window_minutes=15)
        svc.add_report("RPT-001", "USER-A", 12.9716, 77.5946, _ts(-5), "flooding")
        result = svc.find_nearby_reports(12.9720, 77.5950, _ts())
        assert len(result) == 1
        assert result[0].report_id == "RPT-001"

    def test_distant_report_not_found(self):
        svc = CorroborationService(radius_meters=500, time_window_minutes=15)
        svc.add_report("RPT-001", "USER-A", 28.6139, 77.2090, _ts(-5), "flooding")  # Delhi
        result = svc.find_nearby_reports(12.9716, 77.5946, _ts())  # Bangalore
        assert result == []

    def test_old_report_not_found(self):
        svc = CorroborationService(radius_meters=500, time_window_minutes=15)
        svc.add_report("RPT-001", "USER-A", 12.9716, 77.5946, _ts(-60), "flooding")  # 1hr ago
        result = svc.find_nearby_reports(12.9716, 77.5946, _ts())
        assert result == []

    def test_excluded_report_id_not_returned(self):
        svc = CorroborationService(radius_meters=500, time_window_minutes=15)
        svc.add_report("RPT-001", "USER-A", 12.9716, 77.5946, _ts(-5), "flooding")
        result = svc.find_nearby_reports(
            12.9716, 77.5946, _ts(), exclude_report_id="RPT-001"
        )
        assert result == []

    def test_excluded_reporter_not_returned(self):
        svc = CorroborationService(radius_meters=500, time_window_minutes=15)
        svc.add_report("RPT-001", "USER-A", 12.9716, 77.5946, _ts(-5), "flooding")
        result = svc.find_nearby_reports(
            12.9716, 77.5946, _ts(), exclude_reporter="USER-A"
        )
        assert result == []

    def test_multiple_reports_sorted_by_distance(self):
        svc = CorroborationService(radius_meters=1000, time_window_minutes=15)
        svc.add_report("RPT-001", "USER-A", 12.9726, 77.5956, _ts(-5), "flooding")
        svc.add_report("RPT-002", "USER-B", 12.9718, 77.5948, _ts(-3), "flooding")
        result = svc.find_nearby_reports(12.9716, 77.5946, _ts())
        assert len(result) == 2
        assert result[0].distance_meters <= result[1].distance_meters

    def test_count_independent_corroborators(self):
        svc = CorroborationService(radius_meters=500, time_window_minutes=15)
        svc.add_report("RPT-001", "USER-A", 12.9716, 77.5946, _ts(-5), "flooding")
        svc.add_report("RPT-002", "USER-B", 12.9717, 77.5947, _ts(-4), "flooding")
        nearby = svc.find_nearby_reports(12.9716, 77.5946, _ts())
        count = svc.count_independent_corroborators(nearby, "USER-C")
        assert count == 2

    def test_same_reporter_not_counted_as_independent(self):
        svc = CorroborationService(radius_meters=500, time_window_minutes=15)
        svc.add_report("RPT-001", "USER-A", 12.9716, 77.5946, _ts(-5), "flooding")
        nearby = svc.find_nearby_reports(12.9716, 77.5946, _ts())
        count = svc.count_independent_corroborators(nearby, "USER-A")
        assert count == 0
