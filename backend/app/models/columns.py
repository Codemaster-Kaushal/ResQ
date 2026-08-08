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
    - ``native_enum=False`` — emits VARCHAR + CHECK on every dialect instead of a
      Postgres native ENUM type, so adding a member never needs an ALTER TYPE.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )
