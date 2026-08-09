"""
Severity scoring configuration.
All weights and thresholds live here — not in calculation functions.
Adjust here to tune scoring behaviour without changing application logic.
"""

from ai_engine.config import (
    SEVERITY_CRITICAL_THRESHOLD,
    SEVERITY_HIGH_THRESHOLD,
    SEVERITY_MEDIUM_THRESHOLD,
)

# ── Score label thresholds ────────────────────────────────────────────────────
# Derived from config.py (which reads environment variables)
CRITICAL_THRESHOLD: int = SEVERITY_CRITICAL_THRESHOLD   # 80–100
HIGH_THRESHOLD: int = SEVERITY_HIGH_THRESHOLD            # 60–79
MEDIUM_THRESHOLD: int = SEVERITY_MEDIUM_THRESHOLD        # 40–59
# Implicitly: 0–39 = LOW

# ── Risk factor weights ───────────────────────────────────────────────────────
# Each key maps to its maximum contribution (out of 100).
# They must sum to 100 for a fully-weighted score.
#
# Hybrid approach: Granite extracts structured factors → deterministic maths here.
#
# Scoring worked example — 5 trapped, medical, rapidly rising water, confidence=0.9:
#   ai_assessment:   0.9 × 18 = 16.2
#   people(5):       0.9 × 22 = 19.8
#   medical:         1   × 18 = 18.0
#   trapped:         1   × 18 = 18.0
#   immediate:       1   × 14 = 14.0  (rising water)
#   subtotal                  = 86.0  → CRITICAL ✓
RISK_FACTOR_WEIGHTS: dict[str, float] = {
    "ai_incident_assessment":    18.0,   # AI classification confidence contribution
    "people_at_risk":            22.0,
    "medical_emergency":         18.0,
    "trapped_persons":           18.0,
    "immediate_physical_danger": 14.0,   # fire_present OR rapidly_rising_water
    "structural_damage":          6.0,
    "environmental_risk":         2.0,
    "other_context":              2.0,   # infrastructure_failure, evacuation_impossible, vulnerable_people
}

# ── People-at-risk scaling ────────────────────────────────────────────────────
# Map people_at_risk count to a fraction of the "people_at_risk" weight.
# Fractions are clamped to [0, 1].
PEOPLE_AT_RISK_SCALE: list[tuple[int, float]] = [
    (0, 0.0),     # 0 known people → 0%
    (1, 0.45),    # 1 person → 45%
    (2, 0.60),    # 2 people → 60%
    (3, 0.75),    # 3 people → 75%
    (5, 0.90),    # 5 people → 90%
    (10, 1.0),    # 10+ people → 100%
]
