"""Time handling.

**Every datetime stored or compared in this system is naive UTC.**

SQLite's DATETIME type has no timezone slot: a tz-aware datetime written through it
comes back naive, so mixing the two styles raises "can't compare offset-naive and
offset-aware datetimes" deep inside the ageing and corroboration maths. Normalising at
the boundary — here — keeps SQLite and Postgres behaving identically.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """Current UTC time, naive. The default for every timestamp column."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Normalise any datetime to naive UTC.

    Client devices send ISO timestamps with an offset (FR-1); this is where that
    offset is applied and dropped, before the value reaches the database.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def minutes_between(earlier: datetime, later: datetime) -> float:
    """Minutes from `earlier` to `later`. Negative if the arguments are reversed."""
    return (to_naive_utc(later) - to_naive_utc(earlier)) / timedelta(minutes=1)
