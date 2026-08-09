"""
Deterministic rule-based risk factor extractor.
Used as fallback when Granite is unavailable or times out.
"""

import re
import logging

from shared.schemas.severity import RiskFactors

logger = logging.getLogger(__name__)

# ── Number-of-people extraction ───────────────────────────────────────────────

# Written-out number words → digit mapping
_WORD_TO_NUM: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}

_PEOPLE_PATTERNS: list[re.Pattern] = [
    # Digit numbers
    re.compile(r"(\d+)\s+(?:people|persons?|individuals?|victims?|survivors?|residents?)", re.I),
    re.compile(r"(\d+)\s+(?:are\s+)?trapped", re.I),
    re.compile(r"(\d+)\s+(?:are\s+)?(?:injur\w+|hurt|wounded|unconscious)", re.I),
    re.compile(r"(\d+)\s+(?:families|households?)", re.I),
    re.compile(r"(?:family|families)\s+of\s+(\d+)", re.I),
    # Word numbers
    re.compile(r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)\s+(?:people|persons?|individuals?|victims?|survivors?|residents?)", re.I),
    re.compile(r"(one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:are\s+)?trapped", re.I),
    re.compile(r"(one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:are\s+)?(?:injur\w+|hurt|wounded|unconscious)", re.I),
]

# ── Boolean pattern rules ─────────────────────────────────────────────────────
_TRAPPED_PATTERNS = [
    re.compile(r"\btrapped?\b", re.I),
    re.compile(r"\bstuck\b", re.I),
    re.compile(r"cannot\s+escape", re.I),
    re.compile(r"unable\s+to\s+evacuate", re.I),
    re.compile(r"can[' ]?t\s+get\s+out", re.I),
    re.compile(r"waiting\s+for\s+rescue", re.I),
]
_MEDICAL_PATTERNS = [
    re.compile(r"\binjured?\b", re.I),
    re.compile(r"\bbleeding\b", re.I),
    re.compile(r"\bunconscious\b", re.I),
    re.compile(r"heart\s+attack", re.I),
    re.compile(r"medical\s+emergency", re.I),
    re.compile(r"\bhurt\b", re.I),
    re.compile(r"\bwounded\b", re.I),
    re.compile(r"with\s+injur(?:ies|y)", re.I),
]
_RISING_WATER_PATTERNS = [
    re.compile(r"water\s+(?:is\s+)?rising", re.I),
    re.compile(r"rising\s+water", re.I),
    re.compile(r"water\s+level(?:s)?\s+(?:are\s+)?(?:rising|increasing)", re.I),
    re.compile(r"rapidly\s+(?:rising|increasing)\s+water", re.I),
    re.compile(r"floodwater\s+has\s+entered", re.I),
    re.compile(r"ground\s+floor.*flood", re.I),
]
_STRUCTURAL_PATTERNS = [
    re.compile(r"building\s+collapsed?", re.I),
    re.compile(r"roof\s+collapsed?", re.I),
    re.compile(r"wall\s+collapsed?", re.I),
    re.compile(r"structure\s+collapsed?", re.I),
    re.compile(r"structural\s+damage", re.I),
]
_FIRE_PATTERNS = [
    re.compile(r"\bfire\b", re.I),
    re.compile(r"\bburning\b", re.I),
    re.compile(r"\bflames?\b", re.I),
    re.compile(r"\bblaze\b", re.I),
]
_INFRA_PATTERNS = [
    re.compile(r"bridge\s+damaged?", re.I),
    re.compile(r"road\s+blocked?", re.I),
    re.compile(r"power\s+(?:cut|outage|failure|line)", re.I),
    re.compile(r"electricity\s+failure", re.I),
    re.compile(r"infrastructure\s+(?:damaged?|failure|collapsed?)", re.I),
]
_EVACUATION_PATTERNS = [
    re.compile(r"evacuation\s+(?:is\s+)?(?:impossible|blocked|prevented)", re.I),
    re.compile(r"cannot\s+escape", re.I),
    re.compile(r"no\s+way\s+out", re.I),
    re.compile(r"roads?\s+(?:are\s+)?(?:blocked|impassable|closed)", re.I),
    re.compile(r"unable\s+to\s+evacuate", re.I),
]
_VULNERABLE_PATTERNS = [
    re.compile(r"\belderly\b", re.I),
    re.compile(r"\bchild(?:ren)?\b", re.I),
    re.compile(r"\bkid(?:s)?\b", re.I),
    re.compile(r"\bdisabled?\b", re.I),
    re.compile(r"\bpregnant\b", re.I),
    re.compile(r"\binfant(?:s)?\b", re.I),
    re.compile(r"\bbab(?:y|ies)\b", re.I),
]
_ENV_PATTERNS = [
    re.compile(r"\bflooded?\b", re.I),
    re.compile(r"landslide", re.I),
    re.compile(r"mudslide", re.I),
    re.compile(r"hazardous", re.I),
    re.compile(r"chemical\s+(?:spill|leak)", re.I),
    re.compile(r"toxic", re.I),
    re.compile(r"water\s+entered\s+the\s+ground\s+floor", re.I),
]


def _any_match(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def extract_risk_factors_rule_based(description: str) -> RiskFactors:
    """
    Deterministic keyword-based risk factor extraction.
    Used as fallback when Granite is unavailable or too slow.
    Always returns a valid RiskFactors — never raises.
    """
    # ── People count ──────────────────────────────────────────────────────────
    people_at_risk = 0
    for pattern in _PEOPLE_PATTERNS:
        for match in pattern.finditer(description):
            try:
                raw = match.group(1)
                # Handle word numbers
                candidate = _WORD_TO_NUM.get(raw.lower(), None)
                if candidate is None:
                    candidate = int(raw)
                people_at_risk = max(people_at_risk, candidate)
            except (IndexError, ValueError):
                pass

    risk = RiskFactors(
        people_at_risk=people_at_risk,
        trapped_persons=_any_match(_TRAPPED_PATTERNS, description),
        medical_emergency=_any_match(_MEDICAL_PATTERNS, description),
        rapidly_rising_water=_any_match(_RISING_WATER_PATTERNS, description),
        structural_damage=_any_match(_STRUCTURAL_PATTERNS, description),
        fire_present=_any_match(_FIRE_PATTERNS, description),
        infrastructure_failure=_any_match(_INFRA_PATTERNS, description),
        evacuation_impossible=_any_match(_EVACUATION_PATTERNS, description),
        vulnerable_people=_any_match(_VULNERABLE_PATTERNS, description),
        environmental_danger=_any_match(_ENV_PATTERNS, description),
    )

    logger.debug("Rule-based risk extraction result: %s", risk.model_dump())
    return risk
