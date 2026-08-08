"""The scoring pipeline: triage, then authenticity.

Order is not incidental. Authenticity's corroboration test compares incident types, and
its low-information test reuses the incident classifier, so triage has to have run
first. Keeping the sequence in one place stops callers from getting it wrong.
"""

from __future__ import annotations

import uuid

from app.core.logging import get_logger
from app.services.authenticity import assess_pending, assess_report
from app.services.triage import triage_pending, triage_report

logger = get_logger(__name__)


async def process_report(report_id: uuid.UUID, *, force: bool = False) -> None:
    """Score one report end to end. Never raises — both stages swallow their own faults."""
    await triage_report(report_id, force=force)
    await assess_report(report_id, force=force)


async def process_pending(limit: int | None = None) -> tuple[int, int]:
    """Catch up anything unscored. This is the retry queue FR-4 promises.

    Returns (newly triaged, newly assessed).
    """
    triaged = await triage_pending(limit)
    assessed = await assess_pending(limit)
    return triaged, assessed
