"""The deterministic local scorer — the floor beneath every other provider.

Always available, needs no network, and returns the same answer for the same text every
time. The remote providers are an enhancement layer on top of this; if all of them fail
the system degrades to these rules rather than to nothing (TRD §5, NFR-2).

It extracts *signals* only. Weighting happens in ``services/triage.py`` so the
arithmetic is identical whichever provider answered.
"""

from __future__ import annotations

import re

from app.ai.base import LOCAL_PROVIDER_NAME, TriageResult
from app.models.enums import IncidentType

# --- Incident classification -----------------------------------------------------
#
# Patterns are weighted because signals differ in how much they tell you: "landslide"
# names the incident outright, "crack" only hints at one. The heaviest total wins;
# ties break towards the more life-threatening type via INCIDENT_TIEBREAK.

_INCIDENT_PATTERNS: dict[IncidentType, tuple[tuple[str, int], ...]] = {
    IncidentType.TRAPPED_PERSONS: (
        # An explicit "trapped" outranks the structure that trapped them: someone
        # pinned in a flooded basement is a rescue call, not a flooding report.
        (r"\btrapped\b", 6),
        (r"\btrapped (?:in|inside|under|on)\b", 7),
        (r"\bstuck (?:under|inside|in the)\b", 5),
        (r"\bunder the (?:debris|rubble|slab|roof|wall)\b", 5),
        (r"\bfallen into\b|\bfell into\b", 4),
        (r"\bpull(?:ing)? (?:him|her|them|the \w+) out\b", 4),
        (r"\bburied\b", 4),
        (r"\bpinned\b", 4),
        (r"\bcan(?:no|')?t get out\b", 4),
        (r"\bno way (?:out|in)\b", 3),
        # Weak on its own — plenty of safe reports mention people being indoors.
        (r"\bstill inside\b", 2),
    ),
    IncidentType.STRUCTURAL_COLLAPSE: (
        # Requires a structure in context, so "a man collapsed" does not land here.
        (r"\b(?:building|wall|roof|slab|structure|house|floor|bridge|scaffolding|balcony)\b[^.]{0,40}\bcollaps", 6),
        (r"\bcollaps\w*\b[^.]{0,30}\b(?:building|wall|roof|slab|structure|house|bridge)\b", 6),
        # A crack matters far more when it is a crack *in something structural*.
        (r"\b(?:building|wall|roof|slab|structure|house|bridge|pillar|column)\b[^.]{0,30}\bcrack", 4),
        (r"\bcame down\b", 4),
        (r"\bcaved in\b", 4),
        (r"\bgave way\b", 3),
        (r"\bshaking\b|\btremor\b", 3),
        (r"\bleaning\b", 3),
        (r"\bcrack(?:ed|ing|s)?\b", 2),
    ),
    IncidentType.FIRE: (
        (r"\bfire\b", 5),
        (r"\bflames?\b", 5),
        (r"\bblaze\b", 5),
        (r"\bburning\b", 4),
        (r"\bgas (?:leak|cylinder)\b", 4),
        (r"\bsmell of gas\b", 4),
        (r"\bsmoke\b", 3),
    ),
    IncidentType.FLOODING: (
        (r"\bflood(?:ed|ing)?\b", 5),
        (r"\brising water\b|\bwater (?:is |level is )?rising\b", 5),
        (r"\bdrowning\b", 5),
        (r"\bwater ?log(?:ged|ging)?\b", 4),
        (r"\bsubmerged\b", 4),
        (r"\bwater (?:entered|entering|is entering)\b", 4),
        (r"\boverflow(?:ed|ing)?\b", 3),
        (r"\b(?:knee|waist|chest) (?:deep|height)\b", 3),
    ),
    IncidentType.LANDSLIDE: (
        (r"\blandslide\b", 6),
        (r"\bmud ?slide\b", 6),
        (r"\bmud (?:is )?sliding\b", 5),
        (r"\bretaining wall\b", 4),
        (r"\bslope\b", 3),
        (r"\bboulders?\b", 3),
    ),
    IncidentType.MEDICAL: (
        (r"\bnot breathing\b", 6),
        (r"\bunconscious\b", 5),
        (r"\bunresponsive\b", 5),
        (r"\bbleeding\b", 5),
        (r"\bcardiac\b|\bheart attack\b", 5),
        (r"\bin lab(?:ou)?r\b", 5),
        # A person collapsing is a medical call, not a structural one.
        (r"\b(?:man|woman|person|child|boy|girl|elderly|patient|he|she|someone)\b[^.]{0,25}\bcollapsed\b", 6),
        (r"\bfainted\b", 4),
        (r"\bambulance\b", 3),
        (r"\bdiabetic\b", 3),
        (r"\binjured\b", 3),
    ),
    IncidentType.INFRASTRUCTURE: (
        (r"\bstreet ?light\b", 4),
        (r"\btransformer\b", 4),
        (r"\bmanhole\b", 4),
        (r"\bsewage\b", 4),
        (r"\bno power\b|\bpower (?:has been )?(?:out|cut)\b", 4),
        (r"\btree has fallen\b|\bfallen tree\b|\btree (?:has )?fell\b", 4),
        (r"\belectricity\b", 3),
        (r"\broad (?:is )?block(?:ed|ing)?\b|\bblocking both lanes\b", 3),
        (r"\bdrain\b", 2),
    ),
}

# Higher wins a tie. Mirrors the incident weights in TRD §4.1, so an ambiguous report
# is resolved towards the interpretation that risks more lives.
_INCIDENT_TIEBREAK: dict[IncidentType, int] = {
    IncidentType.TRAPPED_PERSONS: 40,
    IncidentType.STRUCTURAL_COLLAPSE: 38,
    IncidentType.MEDICAL: 32,
    IncidentType.FIRE: 30,
    IncidentType.FLOODING: 26,
    IncidentType.LANDSLIDE: 26,
    IncidentType.INFRASTRUCTURE: 14,
    IncidentType.OTHER: 10,
}

# --- Life-risk and vulnerability signals ------------------------------------------
#
# Canonical term keys; triage.py maps them to weights. The taxonomy is TRD §4.1's:
# unconscious, not breathing, bleeding, trapped, drowning, rising water, no exit.

_LIFE_RISK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("not_breathing", r"\bnot breathing\b|\bstopped breathing\b|\bcan(?:no|')?t breathe\b|\bbreathing is (?:very )?shallow\b"),
    ("drowning", r"\bdrown(?:ing|ed)?\b"),
    ("unconscious", r"\bunconscious\b|\bunresponsive\b|\bpassed out\b"),
    ("trapped", r"\btrapped\b|\bstuck under\b|\bburied\b|\bpinned\b|\bunder the (?:debris|rubble|slab)\b"),
    ("bleeding", r"\bbleeding\b|\bhaemorrhag\w*\b|\bhemorrhag\w*\b|\bblood loss\b"),
    ("rising_water", r"\brising water\b|\bwater is rising\b|\bwater level (?:is )?rising\b"),
    ("no_exit", r"\bno way (?:out|in)\b|\bno exit\b|\bcan(?:no|')?t get out\b|\bexit (?:is )?blocked\b"),
)

_VULNERABILITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("children", r"\bchild(?:ren)?\b|\bkids?\b|\bbab(?:y|ies)\b|\binfants?\b|\bschool\b|\bstudents?\b|\btoddlers?\b"),
    ("elderly", r"\belderly\b|\bold (?:man|woman|couple|people)\b|\bsenior citizens?\b|\baged couple\b"),
    ("disabled", r"\bdisabled\b|\bwheelchair\b|\bhandicapped\b|\bimmobile\b"),
    ("pregnant", r"\bpregnant\b|\bin lab(?:ou)?r\b|\bexpecting mother\b"),
    ("injured", r"\binjured\b|\bwounded\b|\bhurt\b|\bfractur\w*\b"),
)

# --- People affected ----------------------------------------------------------------

_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100, "couple": 2, "few": 3, "several": 4, "dozen": 12,
}

_PEOPLE_NOUNS = (
    r"(?:people|persons?|residents?|workers?|passengers?|students?|children|kids|"
    r"famil(?:y|ies)|men|women|individuals|victims|labou?rers?|occupants?)"
)

_COUNTED_PEOPLE = re.compile(
    rf"\b(?:at least |about |around |nearly |over |more than |up to )?"
    rf"(\d{{1,5}}|{'|'.join(_NUMBER_WORDS)})\s+(?:small |young |old |elderly )?{_PEOPLE_NOUNS}\b"
)

_SINGLE_PERSON = re.compile(
    r"\b(?:a|an|one|the)\s+(?:elderly\s+|old\s+|young\s+|small\s+)?"
    r"(?:man|woman|person|child|boy|girl|worker|driver|rider|watchman|patient|"
    r"resident|labou?rer|student)\b"
)

_COMPILED_INCIDENTS = {
    incident: tuple((re.compile(pattern), weight) for pattern, weight in patterns)
    for incident, patterns in _INCIDENT_PATTERNS.items()
}
_COMPILED_LIFE_RISK = tuple((term, re.compile(pattern)) for term, pattern in _LIFE_RISK_PATTERNS)
_COMPILED_VULNERABILITY = tuple(
    (term, re.compile(pattern)) for term, pattern in _VULNERABILITY_PATTERNS
)


def classify_incident(text: str) -> tuple[IncidentType, int]:
    """Pick the incident type and report the winning evidence score."""
    scores: dict[IncidentType, int] = {}

    for incident, patterns in _COMPILED_INCIDENTS.items():
        total = sum(weight for pattern, weight in patterns if pattern.search(text))
        if total:
            scores[incident] = total

    if not scores:
        return IncidentType.OTHER, 0

    best = max(scores.items(), key=lambda item: (item[1], _INCIDENT_TIEBREAK[item[0]]))
    return best[0], best[1]


def detect_life_risk(text: str) -> list[str]:
    return [term for term, pattern in _COMPILED_LIFE_RISK if pattern.search(text)]


def detect_vulnerability(text: str) -> list[str]:
    return [term for term, pattern in _COMPILED_VULNERABILITY if pattern.search(text)]


def estimate_people_affected(text: str) -> int | None:
    """Largest credible head count mentioned, or 1 when a single person is described."""
    counts: list[int] = []

    for raw in _COUNTED_PEOPLE.findall(text):
        if raw.isdigit():
            counts.append(int(raw))
        elif raw in _NUMBER_WORDS:
            counts.append(_NUMBER_WORDS[raw])

    if counts:
        return max(counts)

    if _SINGLE_PERSON.search(text):
        return 1

    return None


class LocalScorer:
    """Rule-based provider. Never raises, never needs a network."""

    name = LOCAL_PROVIDER_NAME

    def is_available(self) -> bool:
        return True

    def classify_sync(self, text: str) -> TriageResult:
        normalised = (text or "").lower()

        incident, evidence = classify_incident(normalised)
        life_risk = detect_life_risk(normalised)
        vulnerability = detect_vulnerability(normalised)
        people = estimate_people_affected(normalised)

        signal_count = evidence + len(life_risk) + len(vulnerability)

        return TriageResult(
            incident_type=incident,
            life_risk_terms=life_risk,
            people_affected_estimate=people,
            vulnerability_terms=vulnerability,
            # Rules cannot see the photograph; the modifier stays neutral (TRD §4.1).
            visual_severity_modifier=0,
            # Capped below 1.0: this is a heuristic and should never present as certain.
            confidence=round(min(0.85, 0.25 + 0.05 * signal_count), 2),
        )

    async def classify(self, text: str, image_bytes: bytes | None = None) -> TriageResult:
        return self.classify_sync(text)
