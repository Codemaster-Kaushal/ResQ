"""Prior-report state for the AI engine's authenticity checks.

The AI engine ships its own `state.json` holding image hashes and recent reports.
That file only ever knows the reports that passed through the engine — it has no
idea about the 40 seeded reports or anything filed before the integration. Left
to itself it would miss the seeded duplicate pair entirely and find no
corroboration cluster.

`AuthenticityEngine.calculate_authenticity()` accepts `previous_reports` and
`known_hashes` as parameters, so the fix is simply to hand it the real table.
This module builds those two structures from `Report`, which makes the database
the single source of truth for duplicates, corroboration and movement — exactly
as it already is for the backend's own authenticity path.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from app.config import settings
from app.core.logging import get_logger
from app.models import Report

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import datetime

logger = get_logger(__name__)

# Only reports filed near the subject in time can corroborate it or contradict
# its reporter's movement, so there is no point loading the whole table.
LOOKBACK_HOURS = 24


def _earlier_than(report: Report) -> tuple[Any, str]:
    """Sort key that breaks receipt-time ties deterministically."""
    return (report.received_at, str(report.id))


def previous_reports_for(session: Session, report: Report) -> list[Any]:
    """Prior reports by the *same pseudonym*, for the impossible-movement check.

    Returns the AI engine's `PreviousReport` dataclass. Imported lazily so this
    module stays importable when the engine is not installed.
    """
    from app.ai.resq_engine import ensure_engine_importable

    if not ensure_engine_importable():
        return []
    from ai_engine.authenticity.movement_check import PreviousReport

    window = report.client_created_at - timedelta(hours=LOOKBACK_HOURS)
    rows = session.exec(
        select(Report).where(
            Report.reporter_pseudonym == report.reporter_pseudonym,
            Report.id != report.id,
            Report.client_created_at >= window,
        )
    ).all()

    return [
        PreviousReport(
            report_id=str(row.id),
            lat=row.lat,
            lon=row.lng,
            timestamp=row.client_created_at,
        )
        for row in rows
        if row.lat is not None and row.lng is not None
    ]


def known_hashes_for(session: Session, report: Report) -> dict[str, str]:
    """Perceptual hashes of images already on file, keyed by report id.

    Only reports received *before* this one are included. The report that
    re-uses an image is the suspicious one; the original must not be penalised
    for having been first — the same rule the backend's own duplicate check
    follows.
    """
    mine = _earlier_than(report)
    rows = session.exec(select(Report).where(Report.image_phash.is_not(None))).all()  # type: ignore[union-attr]

    return {
        str(row.id): row.image_phash
        for row in rows
        if row.id != report.id and _earlier_than(row) < mine and row.image_phash
    }


def corroborating_reports_for(session: Session, report: Report) -> list[dict[str, Any]]:
    """Nearby independent reports, for the corroboration signal.

    Kept as plain dicts because the engine's corroboration service takes a
    loose shape; the filtering that matters (independence) happens here.
    """
    window = timedelta(minutes=settings.corroboration_window_min)
    rows = session.exec(
        select(Report).where(
            Report.id != report.id,
            Report.client_created_at >= report.client_created_at - window,
            Report.client_created_at <= report.client_created_at + window,
        )
    ).all()

    return [
        {
            "report_id": str(row.id),
            "lat": row.lat,
            "lon": row.lng,
            "timestamp": row.client_created_at,
            "incident_type": row.incident_type.value if row.incident_type else None,
            "pseudonym": row.reporter_pseudonym,
        }
        for row in rows
        # A reporter cannot corroborate themselves.
        if row.reporter_pseudonym != report.reporter_pseudonym
    ]


def image_bytes_for(report: Report) -> bytes | None:
    """Read the stored image so the engine can hash it. Missing file is not fatal."""
    if not report.image_path:
        return None
    try:
        return (settings.media_dir / report.image_path).read_bytes()
    except OSError:
        logger.warning(
            "report image unreadable; authenticity will run without it",
            extra={"report_id": str(report.id), "path": report.image_path},
        )
        return None
