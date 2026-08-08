"""The four demo zones.

Real Bengaluru localities, so the coordinates and distances behave sensibly on a map
and the dispatch radius means something.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    code: str
    name: str
    lat: float
    lng: float


ZONES: dict[str, Zone] = {
    "KOR": Zone("KOR", "Koramangala", 12.9352, 77.6245),
    "WHF": Zone("WHF", "Whitefield", 12.9698, 77.7500),
    "HBL": Zone("HBL", "Hebbal", 13.0358, 77.5970),
    "JYN": Zone("JYN", "Jayanagar", 12.9250, 77.5938),
}

# Deliberately outside every zone, for the geo-implausibility fixtures.
ANOMALOUS = "OUT"
