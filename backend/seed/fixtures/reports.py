"""The forty seeded reports.

Twelve are **deliberate fixtures**: each one exists so a specific acceptance criterion
in a later phase has something real to detect. Their ``purpose`` field says which.
The remaining twenty-eight are ordinary traffic, spread across the four zones, so the
deliberate cases have a realistic population to stand out from.

Scores are *not* seeded. Reports arrive at status ``received`` with no severity and no
authenticity, exactly as ingestion leaves them — Phases 4 and 5 compute those, and
pre-filling them would make their acceptance criteria pass without the engines working.
"""

from __future__ import annotations

from dataclasses import dataclass

from seed.fixtures.zones import ANOMALOUS


@dataclass(frozen=True)
class ReportSpec:
    key: str
    zone: str
    text: str
    pseudonym: str
    client_hours_ago: float

    # Metre offsets from the zone centre; ignored when lat/lng are given outright.
    north_m: float = 0.0
    east_m: float = 0.0
    lat: float | None = None
    lng: float | None = None

    # Defaults to client_hours_ago — i.e. the report reached the server immediately.
    received_hours_ago: float | None = None

    image: str | None = None

    # EXIF GPS written into the photograph: "match" places it at the reported
    # coordinates (Phase 5 EXIF_CONSISTENT, +10), "mismatch" places it in another city
    # so the bonus is correctly withheld. None writes no EXIF at all.
    image_gps: str | None = None

    purpose: str = ""

    @property
    def is_deliberate(self) -> bool:
        return bool(self.purpose)


DELIBERATE_SPECS: tuple[ReportSpec, ...] = (
    ReportSpec(
        key="dup-image-a",
        zone="KOR",
        # Deliberately offset ~900 m from the other Koramangala reports. The fixture
        # exists to demonstrate duplicate detection, so it must not accidentally sit
        # inside another report's corroboration radius and test two signals at once.
        north_m=900,
        east_m=900,
        text=(
            "A portion of the old commercial building on 5th block has come down. "
            "Dust everywhere, people are shouting that someone is under the slab."
        ),
        pseudonym="swift-heron-77",
        client_hours_ago=1.6,
        image="collapse-scene",
        purpose="Duplicate image pair (original) — Phase 5 DUPLICATE_IMAGE.",
    ),
    ReportSpec(
        key="dup-image-b",
        zone="KOR",
        north_m=940,
        east_m=860,
        text=(
            "Building collapse on 5th block, sending the photo going around on our "
            "society group. Looks like people are trapped."
        ),
        # A different reporter forwarding the same photograph — the everyday way a
        # duplicate enters the system.
        pseudonym="quiet-falcon-31",
        client_hours_ago=1.4,
        image="collapse-scene-reshared",
        purpose="Duplicate image pair (near-identical re-encode) — Phase 5.",
    ),
    ReportSpec(
        key="stale-timestamp",
        zone="WHF",
        north_m=-200,
        east_m=310,
        text=(
            "Water entered the ground floor flats last night and the residents moved "
            "upstairs. Only getting signal now to send this."
        ),
        pseudonym="patient-egret-05",
        client_hours_ago=8.5,
        received_hours_ago=0.4,
        purpose="Client clock >6 h before receipt — Phase 5 STALE_REPORT.",
    ),
    ReportSpec(
        key="latest-critical",
        zone="JYN",
        north_m=-90,
        east_m=140,
        text=(
            "The four storey building next to the market has collapsed completely. "
            "At least twenty people are trapped inside, we can hear children crying "
            "under the debris and there is no way in from the front."
        ),
        pseudonym="urgent-kestrel-88",
        # Newest report in the dataset: filed last, must still rank first.
        client_hours_ago=0.02,
        image="collapse-major",
        image_gps="match",
        purpose="Highest severity, newest timestamp — Phase 6 severity must beat FIFO.",
    ),
    ReportSpec(
        key="corroborated-1",
        zone="HBL",
        north_m=40,
        east_m=60,
        text="Thick smoke coming out of the chemical godown near the service road, spreading fast.",
        pseudonym="alert-ibis-19",
        client_hours_ago=0.9,
        image="fire-godown",
        purpose="Corroboration cluster 1/3 — Phase 5 CORROBORATED.",
    ),
    ReportSpec(
        key="corroborated-2",
        zone="HBL",
        north_m=-70,
        east_m=150,
        text="Fire at the godown behind the bus depot, flames now visible above the roof.",
        pseudonym="steady-crane-44",
        client_hours_ago=0.75,
        purpose="Corroboration cluster 2/3 — independent reporter, same event.",
    ),
    ReportSpec(
        key="corroborated-3",
        zone="HBL",
        north_m=110,
        east_m=-30,
        text="Big fire near Hebbal service road, workers running out of the building.",
        pseudonym="bright-plover-63",
        client_hours_ago=0.6,
        purpose="Corroboration cluster 3/3 — independent reporter, same event.",
    ),
    ReportSpec(
        key="null-island",
        zone=ANOMALOUS,
        lat=0.0,
        lng=0.0,
        text="Building shaking badly, need help immediately.",
        pseudonym="lost-swift-02",
        client_hours_ago=1.1,
        purpose="Null-island coordinates — Phase 5 GEO_IMPLAUSIBLE.",
    ),
    ReportSpec(
        key="impossible-move-a",
        zone="KOR",
        north_m=-260,
        east_m=90,
        text="Scooter and a car collided at the junction, one person is on the road bleeding.",
        pseudonym="roaming-shrike-51",
        client_hours_ago=2.0,
        purpose="Impossible movement 1/2 — same pseudonym as the Mumbai report.",
    ),
    ReportSpec(
        key="impossible-move-b",
        zone=ANOMALOUS,
        # Mumbai — roughly 840 km from Bengaluru, filed six minutes later.
        lat=19.0760,
        lng=72.8777,
        text="Water logging near the station, buses have stopped running.",
        pseudonym="roaming-shrike-51",
        client_hours_ago=1.9,
        purpose="Impossible movement 2/2 — >100 km in 6 min, Phase 5.",
    ),
    ReportSpec(
        key="low-information",
        zone="WHF",
        north_m=330,
        east_m=-210,
        text="help",
        pseudonym="brief-tern-08",
        client_hours_ago=1.2,
        purpose="Under 5 tokens, no incident term — Phase 5 LOW_INFORMATION.",
    ),
    ReportSpec(
        key="aged-low-severity",
        zone="JYN",
        north_m=210,
        east_m=-160,
        text="The street light outside our gate has been off for a week and the lane is very dark at night.",
        pseudonym="calm-wagtail-27",
        # Low severity, long wait: ageing must lift it rather than starve it (FR-17).
        client_hours_ago=4.0,
        purpose="Low severity, long wait — Phase 6 ageing prevents starvation.",
    ),
)


# Ordinary traffic. (zone, text, pseudonym, hours_ago, north_m, east_m, image)
_FILLER: tuple[tuple[str, str, str, float, float, float, str | None], ...] = (
    # --- Koramangala ---
    ("KOR", "Three storey building has partially collapsed on 5th block, at least six people "
            "are trapped under the debris and we can hear shouting.",
     "keen-avocet-11", 2.4, 420.0, 300.0, "collapse-street"),
    ("KOR", "Water is rising fast in the underpass, two cars are stuck and the drivers cannot "
            "open their doors.",
     "rapid-godwit-34", 1.8, -350.0, 220.0, None),
    ("KOR", "An elderly man has collapsed at the bus stop, he is unconscious and his breathing "
            "is very shallow.",
     "gentle-siskin-72", 0.7, 180.0, 410.0, None),
    ("KOR", "The transformer on the main road is sparking heavily and the pole is leaning over "
            "the footpath.",
     "sharp-linnet-58", 3.1, -120.0, -380.0, "infra-transformer"),
    ("KOR", "Kitchen fire in a restaurant on 6th block, smoke is spreading into the flats above.",
     "quick-merlin-46", 1.0, 260.0, -150.0, None),
    ("KOR", "The street light near the park entrance has been out for two days.",
     "mild-serin-90", 5.2, -430.0, -260.0, None),
    ("KOR", "The boundary wall of the school has cracked badly after the rain and the children "
            "are still inside the building.",
     "watchful-pipit-23", 1.3, 340.0, 120.0, None),

    # --- Whitefield ---
    ("WHF", "The apartment basement is flooded to chest height and the watchman is trapped in "
            "the pump room.",
     "urgent-dunlin-15", 1.5, 240.0, -300.0, "flood-basement"),
    ("WHF", "Two wheeler accident near the tech park gate, the rider is bleeding heavily from "
            "his head.",
     "steady-knot-67", 0.8, -180.0, 420.0, None),
    ("WHF", "Godown fire spreading near the main road, thick black smoke and workers are still "
            "inside.",
     "alarmed-ruff-39", 2.1, 390.0, 260.0, "fire-warehouse"),
    ("WHF", "The storm water drain has overflowed onto the main road, knee deep water across "
            "two lanes.",
     "plain-stint-81", 3.6, -420.0, -180.0, None),
    ("WHF", "A pregnant woman is in labour and stuck in traffic near the tech park, she needs "
            "an ambulance urgently.",
     "caring-snipe-52", 0.5, 150.0, 340.0, None),
    ("WHF", "A tree has fallen across the road near the lake and is blocking both lanes.",
     "quiet-turnstone-29", 4.4, -260.0, 190.0, None),
    ("WHF", "Scaffolding has collapsed at the construction site, one worker is injured and "
            "another is trapped on the upper level.",
     "urgent-curlew-04", 1.1, 310.0, -240.0, "collapse-site"),
    ("WHF", "There has been no power in the whole layout since morning.",
     "patient-lapwing-76", 6.0, -330.0, -400.0, None),

    # --- Hebbal ---
    ("HBL", "The retaining wall along the flyover has given way and mud is sliding onto the "
            "service road.",
     "sharp-oriole-13", 2.8, 380.0, -290.0, "landslide-wall"),
    ("HBL", "A child has fallen into an open drain near the market and people are trying to "
            "pull him out.",
     "urgent-bunting-60", 0.4, -200.0, 330.0, None),
    ("HBL", "The lake bund is overflowing and water is entering the ground floor houses in the "
            "colony.",
     "steady-thrush-85", 3.3, 420.0, 180.0, None),
    ("HBL", "Strong smell of gas from a house on the corner, a family with two small children "
            "is still inside.",
     "alert-warbler-37", 0.65, -360.0, -120.0, None),
    ("HBL", "The manhole cover on the main road is missing and someone is going to fall in.",
     "plain-dipper-94", 5.8, 130.0, 440.0, None),
    ("HBL", "The slab of a two storey house has collapsed, an elderly couple were living there.",
     "grave-redstart-21", 1.9, -430.0, 240.0, "collapse-house"),

    # --- Jayanagar ---
    ("JYN", "Fire on the second floor of the commercial complex, people are on the terrace "
            "waving for help.",
     "urgent-rosefinch-48", 0.9, 300.0, 350.0, "fire-complex"),
    ("JYN", "The old building near the market has developed a large crack and is leaning to "
            "one side.",
     "careful-nuthatch-56", 3.9, -240.0, -310.0, None),
    ("JYN", "A woman fainted in the queue at the hospital, she is diabetic and unresponsive.",
     "kind-firecrest-70", 1.05, 190.0, -420.0, None),
    ("JYN", "Sewage has been overflowing on our residential street for the third day.",
     "weary-treecreeper-83", 7.1, -390.0, 150.0, None),
    ("JYN", "An auto rickshaw has overturned near the flyover, the driver is bleeding and "
            "cannot move his legs.",
     "swift-whinchat-32", 0.55, 410.0, -200.0, None),
    ("JYN", "Water logging across the market area, the shops are flooded up to knee height.",
     "damp-wheatear-64", 2.6, -150.0, 380.0, None),
    ("JYN", "A compound wall has collapsed onto a parked car, nobody appears to be hurt.",
     "calm-stonechat-17", 4.8, 260.0, 210.0, None),
)

# Filler reports whose photograph carries EXIF GPS, by index. Kept apart from the
# tuples above so the common case stays readable.
_FILLER_IMAGE_GPS: dict[int, str] = {
    8: "match",  # flooded basement — EXIF agrees with the reported location, +10
    10: "mismatch",  # warehouse fire — EXIF is in another city, so no bonus is given
}

FILLER_SPECS: tuple[ReportSpec, ...] = tuple(
    ReportSpec(
        key=f"filler-{index:02d}",
        zone=zone,
        text=text,
        pseudonym=pseudonym,
        client_hours_ago=hours_ago,
        north_m=north_m,
        east_m=east_m,
        image=image,
        image_gps=_FILLER_IMAGE_GPS.get(index) if image else None,
    )
    for index, (zone, text, pseudonym, hours_ago, north_m, east_m, image) in enumerate(
        _FILLER, start=1
    )
)

REPORT_SPECS: tuple[ReportSpec, ...] = DELIBERATE_SPECS + FILLER_SPECS

# Asserted by the seed run, so a careless edit to the lists above fails loudly.
EXPECTED_REPORT_COUNT = 40
EXPECTED_ZONE_COUNTS = {"KOR": 10, "WHF": 10, "HBL": 9, "JYN": 9, ANOMALOUS: 2}
