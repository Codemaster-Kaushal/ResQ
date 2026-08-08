"""Shared column type helpers."""

from __future__ import annotations

from enum import Enum

from sqlalchemy import Enum as SAEnum


def enum_type(enum_cls: type[Enum], length: int = 32) -> SAEnum:
    """A portable enum column that stores the enum's *value*.

    Two defaults are deliberately overridden:

    - ``values_callable`` — SQLAlchemy otherwise persists the member *name*
      (``STRUCTURAL_COLLAPSE``), while the API contract uses the value
      (``structural_collapse``). Without this the database and the JSON disagree.
    - ``native_enum=False`` — emits a plain VARCHAR on every dialect instead of a
      Postgres native ENUM type, so adding a member never needs an ALTER TYPE.

    No CHECK constraint is emitted either (SQLAlchemy's default since 1.4), and that is
    deliberate rather than an oversight: a CHECK listing the allowed values would need
    a migration to extend for exactly the same reason a native ENUM does. Validity is
    enforced at the application boundary instead — ``validate_strings`` rejects unknown
    values on the way in, and Pydantic rejects them at the API edge.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )
