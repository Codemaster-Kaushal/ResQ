"""Offline batch sync (FR-26 … FR-28).

A phone with no signal queues reports locally and posts the lot when it reconnects.
Three things make that safe:

- **Idempotent.** Dedup is by the client's own key, so re-sending a batch the server
  already has is a no-op. A sync that times out halfway can simply be retried.
- **Partial success.** One malformed report does not cost the other nineteen. Each
  item gets its own outcome.
- **The client clock is preserved.** A report filed an hour ago has been waiting an
  hour, whatever time it managed to reach the server (FR-28). Ageing uses that, so
  syncing late never costs a reporter their place.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlmodel import Session, select

from app.config import settings
from app.core.errors import ErrorCode, ErrorEnvelope, ValidationFailedError
from app.core.geo import is_valid_coordinate
from app.core.logging import get_logger
from app.core.time import minutes_between, utcnow
from app.db import get_session
from app.models import Activity, Report, ReportStatus
from app.schemas.sync import (
    SyncBatch,
    SyncItemResult,
    SyncOutcome,
    SyncReportItem,
    SyncResult,
)
from app.services.events import emit_event
from app.services.pipeline import process_report

logger = get_logger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


def _rejected(item: SyncReportItem, code: str, message: str) -> SyncItemResult:
    return SyncItemResult(
        idempotency_key=item.idempotency_key,
        outcome=SyncOutcome.REJECTED,
        error_code=code,
        error_message=message,
    )


@router.post(
    "/reports",
    response_model=SyncResult,
    status_code=status.HTTP_200_OK,
    summary="Sync a batch of offline-queued reports",
    responses={
        413: {"model": ErrorEnvelope, "description": "Batch too large"},
        422: {"model": ErrorEnvelope},
    },
)
async def sync_reports(
    payload: SyncBatch,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> SyncResult:
    """Accept everything a device queued while it was offline.

    Returns 200 with a per-item breakdown rather than failing the request, because a
    batch is rarely all-or-nothing. Scoring runs afterwards in the background, exactly
    as it does for a single report.
    """
    if len(payload.reports) > settings.sync_max_batch_size:
        raise ValidationFailedError(
            "Batch exceeds the maximum size",
            code=ErrorCode.VALIDATION_ERROR,
            status_code=413,
            detail={
                "received": len(payload.reports),
                "limit": settings.sync_max_batch_size,
                "hint": "split the queue across several requests; each one is idempotent",
            },
        )

    received_at = utcnow()
    results: list[SyncItemResult] = []
    to_score: list = []

    # Everything already on file, fetched once rather than per item.
    incoming_keys = [item.idempotency_key for item in payload.reports]
    existing = {
        report.idempotency_key: report
        for report in session.exec(
            select(Report).where(Report.idempotency_key.in_(incoming_keys))  # type: ignore[union-attr]
        ).all()
    }

    # A batch can contain the same key twice if the device double-queued it.
    seen_in_batch: dict[str, Report] = {}

    for item in payload.reports:
        already = existing.get(item.idempotency_key) or seen_in_batch.get(item.idempotency_key)
        if already is not None:
            results.append(
                SyncItemResult(
                    idempotency_key=item.idempotency_key,
                    outcome=SyncOutcome.DUPLICATE,
                    report_id=already.id,
                    status=already.status,
                    client_created_at=already.client_created_at,
                    minutes_waiting=round(
                        minutes_between(already.client_created_at, received_at), 1
                    ),
                )
            )
            continue

        if not is_valid_coordinate(item.lat, item.lng):
            results.append(
                _rejected(
                    item,
                    ErrorCode.INVALID_COORDINATES,
                    f"Coordinates ({item.lat}, {item.lng}) are outside the valid range",
                )
            )
            continue

        supplied = item.client_created_at is not None
        client_time = item.normalised_client_time() or received_at

        report = Report(
            idempotency_key=item.idempotency_key,
            text=item.text,
            lat=item.lat,
            lng=item.lng,
            # Kept apart from received_at on purpose: this is what ageing reads.
            client_created_at=client_time,
            received_at=received_at,
            reporter_pseudonym=item.reporter_pseudonym or f"anon-{item.idempotency_key[:8]}",
            status=ReportStatus.RECEIVED,
        )
        session.add(report)
        session.flush()  # the event's case_id is a foreign key

        emit_event(
            session,
            case_id=report.id,
            activity=Activity.REPORT_RECEIVED,
            resource=f"reporter:{report.reporter_pseudonym}",
            metadata={
                "idempotency_key": report.idempotency_key,
                "channel": "offline_sync",
                "device_id": payload.device_id,
                "offline_delay_minutes": round(
                    minutes_between(report.client_created_at, report.received_at), 1
                ),
            },
            timestamp=received_at,
        )

        seen_in_batch[item.idempotency_key] = report
        to_score.append(report)
        results.append(
            SyncItemResult(
                idempotency_key=item.idempotency_key,
                outcome=SyncOutcome.CREATED,
                report_id=report.id,
                status=report.status,
                client_created_at=report.client_created_at,
                minutes_waiting=round(minutes_between(client_time, received_at), 1),
                client_timestamp_supplied=supplied,
            )
        )

    session.commit()

    for report in to_score:
        background.add_task(process_report, report.id)

    created = sum(1 for r in results if r.outcome == SyncOutcome.CREATED)
    duplicates = sum(1 for r in results if r.outcome == SyncOutcome.DUPLICATE)
    rejected = sum(1 for r in results if r.outcome == SyncOutcome.REJECTED)

    logger.info(
        "offline batch synced",
        extra={
            "device_id": payload.device_id,
            "received_count": len(results),
            # Not "created": that is a reserved LogRecord attribute (the record's
            # own creation time) and logging raises rather than shadowing it.
            "created_count": created,
            "duplicate_count": duplicates,
            "rejected_count": rejected,
        },
    )

    return SyncResult(
        received=len(results),
        created=created,
        duplicates=duplicates,
        rejected=rejected,
        results=results,
    )
