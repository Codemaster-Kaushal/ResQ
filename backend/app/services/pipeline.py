"""The scoring pipeline: triage, then authenticity, then the queue.

Order is not incidental. Authenticity's corroboration test compares incident types, and
its low-information test reuses the incident classifier, so triage has to have run
first; and only a report that survived authenticity may enter the queue. Keeping the
sequence in one place stops callers from getting it wrong.
"""

from __future__ import annotations

import uuid

from sqlmodel import Session

from app.core.logging import get_logger
from app.db import engine
from app.models import Report
from app.config import settings
from app.services.authenticity import assess_pending, assess_report
from app.services.priority import enqueue_pending, enqueue_verified
from app.services.triage import rescore_pending, rescore_with_ai, triage_pending, triage_report

logger = get_logger(__name__)


def _enqueue(report_id: uuid.UUID) -> None:
    """Move a freshly verified report into the queue. Flagged reports wait for a human."""
    try:
        with Session(engine) as session:
            report = session.get(Report, report_id)
            if report is not None and enqueue_verified(session, report):
                session.commit()
    except Exception:  # noqa: BLE001 — a background step must never take the app down
        logger.exception("queueing failed; report left verified", extra={"report_id": str(report_id)})


async def process_report(report_id: uuid.UUID, *, force: bool = False) -> None:
    """Score one report end to end. Never raises — each stage swallows its own faults.

    Pass 1 (rules) and authenticity both complete before this returns, so the
    report is ranked and dispatchable immediately. Granite runs last, on a
    report that is already in the queue, and only when it could change the
    outcome — see `needs_ai_rescore`.
    """
    await triage_report(report_id, force=force)
    await assess_report(report_id, force=force)
    _enqueue(report_id)

    # Pass 2. Deliberately after enqueueing: nobody's help waits on the model.
    if settings.ai_engine_enabled:
        await rescore_with_ai(report_id)


async def process_pending(limit: int | None = None) -> tuple[int, int, int]:
    """Catch up anything unscored. This is the retry queue FR-4 promises.

    Returns (newly triaged, newly assessed, newly queued). The AI sweep is a
    separate call so the seed can run the deterministic path only — an LLM
    behind a timeout is not reproducible, and NFR-4 requires the demo dataset
    to be.
    """
    triaged = await triage_pending(limit)
    assessed = await assess_pending(limit)
    queued = enqueue_pending(limit)
    return triaged, assessed, queued


async def rescore_backlog(limit: int | None = None) -> int:
    """Ask the model to revisit anything the rules were unsure about."""
    return await rescore_pending(limit)
