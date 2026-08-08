"""The eight seeded responders.

Positions are chosen so the Phase 7 dispatch criteria are actually demonstrable:

- ``Structural Crew Echo`` sits closer to the Koramangala incidents than
  ``Medical Unit Alpha`` does. A medical incident there must still go to Alpha —
  skill match has to beat raw proximity, which is the Phase 7 acceptance criterion.
- ``Medical Unit Hotel`` starts at capacity, so the capacity filter has something
  to exclude.
- ``Rescue Team Golf`` starts offline, so the availability filter does too.

A non-zero ``active_count`` with no matching Assignment row is intentional, not drift:
it represents workload a unit already had before RescueNet started tracking it. Phase 7
onwards keeps the counter in step with the assignments the system itself creates.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import ResponderSkill, ResponderStatus


@dataclass(frozen=True)
class ResponderSpec:
    key: str
    name: str
    skill: ResponderSkill
    zone: str
    lat: float
    lng: float
    capacity: int
    active_count: int
    status: ResponderStatus
    purpose: str = ""


RESPONDER_SPECS: tuple[ResponderSpec, ...] = (
    ResponderSpec(
        key="medical-alpha",
        name="Medical Unit Alpha",
        skill=ResponderSkill.MEDICAL,
        zone="KOR",
        # Stationed on the far side of the zone, so Echo really is the nearest unit to
        # the Koramangala incidents. Without that separation the "skill beats
        # proximity" case cannot be demonstrated — the matched unit happens to be
        # closest anyway and the test proves nothing.
        lat=12.9410,
        lng=77.6310,
        capacity=3,
        active_count=0,
        status=ResponderStatus.AVAILABLE,
        purpose="Farther from Koramangala incidents than Echo, but skill-matched.",
    ),
    ResponderSpec(
        key="structural-echo",
        name="Structural Crew Echo",
        skill=ResponderSkill.STRUCTURAL,
        zone="KOR",
        lat=12.9358,
        lng=77.6252,
        capacity=2,
        active_count=0,
        status=ResponderStatus.AVAILABLE,
        purpose="Nearest unit to Koramangala; wrong skill for medical calls.",
    ),
    ResponderSpec(
        key="rescue-delta",
        name="Rescue Team Delta",
        skill=ResponderSkill.RESCUE,
        zone="WHF",
        lat=12.9705,
        lng=77.7480,
        capacity=3,
        active_count=1,
        status=ResponderStatus.AVAILABLE,
        purpose="Partially loaded — exercises the load component of match_score.",
    ),
    ResponderSpec(
        key="medical-hotel",
        name="Medical Unit Hotel",
        skill=ResponderSkill.MEDICAL,
        zone="WHF",
        lat=12.9690,
        lng=77.7520,
        capacity=1,
        active_count=1,
        status=ResponderStatus.BUSY,
        purpose="At capacity — must never be assigned (FR-20).",
    ),
    ResponderSpec(
        key="rescue-golf",
        name="Rescue Team Golf",
        skill=ResponderSkill.RESCUE,
        zone="HBL",
        lat=13.0362,
        lng=77.5985,
        capacity=4,
        active_count=0,
        status=ResponderStatus.OFFLINE,
        purpose="Offline with spare capacity — must never be assigned.",
    ),
    ResponderSpec(
        key="structural-foxtrot",
        name="Structural Crew Foxtrot",
        skill=ResponderSkill.STRUCTURAL,
        zone="HBL",
        lat=13.0340,
        lng=77.5950,
        capacity=2,
        active_count=0,
        status=ResponderStatus.AVAILABLE,
        purpose="Only available unit in Hebbal.",
    ),
    ResponderSpec(
        key="medical-bravo",
        name="Medical Unit Bravo",
        skill=ResponderSkill.MEDICAL,
        zone="JYN",
        lat=12.9262,
        lng=77.5950,
        capacity=2,
        active_count=0,
        status=ResponderStatus.AVAILABLE,
        purpose="",
    ),
    ResponderSpec(
        key="rescue-charlie",
        name="Rescue Team Charlie",
        skill=ResponderSkill.RESCUE,
        zone="JYN",
        lat=12.9230,
        lng=77.5920,
        capacity=2,
        active_count=0,
        status=ResponderStatus.AVAILABLE,
        purpose="",
    ),
)
