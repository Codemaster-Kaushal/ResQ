"""Report ingestion and retrieval (FR-1 … FR-5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func
from sqlmodel import Session, select

from app.config import settings
from app.core.errors import (
    ConflictError,
    ErrorCode,
    ErrorEnvelope,
    NotFoundError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.core.time import minutes_between, utcnow
from app.db import get_session
from app.models import Activity, IncidentType, Report, ReportStatus
from app.schemas.report import (
    ExifRead,
    ImageRead,
    ReportCreate,
    ReportCreated,
    ReportPage,
    ReportRead,
    ReportSummary,
    ReviewDecision,
    ReviewRequest,
    ReviewResult,
)
from app.services.events import emit_event
from app.services.media import read_exif_from_path, store_image_bytes
from app.services.pipeline import process_report
from app.services.priority import enqueue_verified
from app.services.triage import reason

logger = get_logger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])

MAX_PAGE_SIZE = 200


def _image_read(report: Report) -> ImageRead | None:
    """Rebuild the image block for a stored report.

    EXIF is re-read from the file rather than duplicated into a column: the file is the
    record FR-5 requires be preserved, and a copy in the database could drift from it.
    """
    if not report.image_path:
        return None

    snapshot = read_exif_from_path(settings.media_dir / report.image_path)
    return ImageRead(
        path=report.image_path,
        phash=report.image_phash or "",
        exif=ExifRead.from_snapshot(snapshot),
    )


def _detail(report: Report) -> ReportRead:
    payload = ReportRead.model_validate(report)
    payload.image = _image_read(report)
    return payload


def _parse_bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    """Parse ``min_lng,min_lat,max_lng,max_lat`` (GeoJSON axis order)."""
    if raw is None:
        return None

    parts = [piece.strip() for piece in raw.split(",")]
    if len(parts) != 4:
        raise ValidationFailedError(
            "bbox must contain exactly four comma-separated numbers",
            code=ErrorCode.INVALID_BBOX,
            detail={"expected": "min_lng,min_lat,max_lng,max_lat", "received": raw},
        )

    try:
        min_lng, min_lat, max_lng, max_lat = (float(piece) for piece in parts)
    except ValueError:
        raise ValidationFailedError(
            "bbox values must be numbers",
            code=ErrorCode.INVALID_BBOX,
            detail={"received": raw},
        ) from None

    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ValidationFailedError(
            "bbox latitudes must be between -90 and 90",
            code=ErrorCode.INVALID_BBOX,
            detail={"min_lat": min_lat, "max_lat": max_lat},
        )
    if not (-180 <= min_lng <= 180 and -180 <= max_lng <= 180):
        raise ValidationFailedError(
            "bbox longitudes must be between -180 and 180",
            code=ErrorCode.INVALID_BBOX,
            detail={"min_lng": min_lng, "max_lng": max_lng},
        )
    if min_lat > max_lat or min_lng > max_lng:
        raise ValidationFailedError(
            "bbox minimums must not exceed their maximums",
            code=ErrorCode.INVALID_BBOX,
            detail={"received": raw},
        )

    return min_lng, min_lat, max_lng, max_lat


@router.post(
    "",
    response_model=ReportCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a report",
    responses={
        200: {"model": ReportCreated, "description": "Idempotency key already on file"},
        413: {"model": ErrorEnvelope, "description": "Image too large"},
        415: {"model": ErrorEnvelope, "description": "Unsupported image type"},
        422: {"model": ErrorEnvelope, "description": "Validation error"},
    },
)
async def create_report(
    response: Response,
    background: BackgroundTasks,
    # Declared field by field rather than as a single Form model: alongside a File
    # parameter FastAPI stops flattening the model and starts expecting a form field
    # named after it. Spelled out, the constraints also produce accurate error
    # locations (`body.lat`) and a readable form in /docs.
    text: Annotated[str, Form(min_length=1, max_length=5000)],
    lat: Annotated[float, Form(ge=-90, le=90)],
    lng: Annotated[float, Form(ge=-180, le=180)],
    client_created_at: Annotated[
        datetime | None, Form(description="Time on the reporter's device")
    ] = None,
    reporter_pseudonym: Annotated[str | None, Form(max_length=64)] = None,
    idempotency_key: Annotated[str | None, Form(max_length=128)] = None,
    # A phone downscales the photo before upload to save radio time and battery,
    # and re-encoding strips EXIF. These carry the coordinates read from the
    # original so the EXIF_CONSISTENT trust signal is not silently lost. Used
    # only when the uploaded file has no EXIF of its own.
    exif_lat: Annotated[float | None, Form(ge=-90, le=90)] = None,
    exif_lng: Annotated[float | None, Form(ge=-180, le=180)] = None,
    image: Annotated[UploadFile | None, File(description="Optional photograph")] = None,
    session: Session = Depends(get_session),
) -> ReportCreated:
    """Accept one report, with or without a photograph.

    Ingestion never scores. The report is persisted at status ``received`` and Phase 4
    attaches triage as a background task from here, so a scoring outage can delay a
    score but can never reject a report (FR-4, NFR-1).
    """
    payload = ReportCreate(
        text=text,
        lat=lat,
        lng=lng,
        client_created_at=client_created_at,
        reporter_pseudonym=reporter_pseudonym,
        idempotency_key=idempotency_key,
    )
    key = payload.resolved_idempotency_key()

    existing = session.exec(
        select(Report).where(Report.idempotency_key == key)
    ).first()
    if existing is not None:
        # Re-submitting a key is a no-op that returns the original (FR-27). This is what
        # makes a retried request on a flaky connection safe.
        logger.info(
            "duplicate idempotency key ignored",
            extra={"idempotency_key": key, "report_id": str(existing.id)},
        )
        response.status_code = status.HTTP_200_OK
        return ReportCreated(
            id=existing.id,
            idempotency_key=existing.idempotency_key,
            status=existing.status,
            received_at=existing.received_at,
            client_created_at=existing.client_created_at,
            reporter_pseudonym=existing.reporter_pseudonym,
            duplicate=True,
            image=_image_read(existing),
        )

    stored = None
    if image is not None and image.filename:
        stored = store_image_bytes(
            await image.read(),
            image.content_type,
            fallback_gps=(exif_lat, exif_lng) if exif_lat is not None and exif_lng is not None else None,
        )

    report = Report(
        idempotency_key=key,
        text=payload.text,
        image_path=stored.relative_path if stored else None,
        image_phash=stored.phash if stored else None,
        lat=payload.lat,
        lng=payload.lng,
        client_created_at=payload.resolved_client_created_at(),
        received_at=utcnow(),
        reporter_pseudonym=payload.resolved_pseudonym(),
        status=ReportStatus.RECEIVED,
    )
    session.add(report)
    # Flush before the event: ProcessEvent.case_id is a foreign key, and with no ORM
    # relationship between the two SQLAlchemy has no dependency to order the inserts by.
    # This is the only place a case and its first event are created together.
    session.flush()

    emit_event(
        session,
        case_id=report.id,
        activity=Activity.REPORT_RECEIVED,
        resource=f"reporter:{report.reporter_pseudonym}",
        metadata={
            "idempotency_key": report.idempotency_key,
            "has_image": stored is not None,
            "offline_delay_minutes": round(
                minutes_between(report.client_created_at, report.received_at), 1
            ),
        },
        timestamp=report.received_at,
    )
    session.commit()
    session.refresh(report)

    logger.info(
        "report received",
        extra={
            "report_id": str(report.id),
            "idempotency_key": report.idempotency_key,
            "has_image": stored is not None,
            "lat": report.lat,
            "lng": report.lng,
        },
    )

    # Scoring runs after the response is sent. The report is already durable, so a slow
    # or unavailable model delays its score without ever costing it (FR-4, NFR-1).
    background.add_task(process_report, report.id)

    return ReportCreated(
        id=report.id,
        idempotency_key=report.idempotency_key,
        status=report.status,
        received_at=report.received_at,
        client_created_at=report.client_created_at,
        reporter_pseudonym=report.reporter_pseudonym,
        duplicate=False,
        image=ImageRead.from_stored(stored) if stored else None,
    )


@router.get(
    "",
    response_model=ReportPage,
    summary="List reports",
    responses={422: {"model": ErrorEnvelope}},
)
def list_reports(
    session: Session = Depends(get_session),
    report_status: Annotated[
        ReportStatus | None, Query(alias="status", description="Filter by lifecycle status")
    ] = None,
    incident_type: Annotated[IncidentType | None, Query()] = None,
    bbox: Annotated[
        str | None,
        Query(description="min_lng,min_lat,max_lng,max_lat", examples=["77.5,12.9,77.8,13.1"]),
    ] = None,
    reporter_pseudonym: Annotated[str | None, Query(max_length=64)] = None,
    has_image: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReportPage:
    conditions: list[Any] = []

    if report_status is not None:
        conditions.append(Report.status == report_status)
    if incident_type is not None:
        conditions.append(Report.incident_type == incident_type)
    if reporter_pseudonym is not None:
        conditions.append(Report.reporter_pseudonym == reporter_pseudonym)
    if has_image is not None:
        conditions.append(
            Report.image_path.is_not(None) if has_image else Report.image_path.is_(None)
        )

    parsed_bbox = _parse_bbox(bbox)
    if parsed_bbox is not None:
        min_lng, min_lat, max_lng, max_lat = parsed_bbox
        conditions.extend(
            [
                Report.lng >= min_lng,
                Report.lng <= max_lng,
                Report.lat >= min_lat,
                Report.lat <= max_lat,
            ]
        )

    total = session.exec(
        select(func.count()).select_from(Report).where(*conditions)
    ).one()

    rows = session.exec(
        select(Report)
        .where(*conditions)
        .order_by(Report.received_at.desc(), Report.id)
        .limit(limit)
        .offset(offset)
    ).all()

    items = []
    for row in rows:
        summary = ReportSummary.model_validate(row)
        summary.has_image = row.image_path is not None
        items.append(summary)

    return ReportPage(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/{report_id}/review",
    response_model=ReviewResult,
    summary="Resolve a flagged report (human review)",
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope, "description": "Report is not awaiting review"},
        422: {"model": ErrorEnvelope},
    },
)
def review_report(
    report_id: uuid.UUID,
    payload: ReviewRequest,
    session: Session = Depends(get_session),
) -> ReviewResult:
    """Verify or reject a report that automated scoring sent to review.

    This is the **only** path to `rejected` (FR-15). Automated scoring can lower a
    report's trust and route it here, but it can never discard it: during a
    mass-casualty event the cost of silently dropping one true report far exceeds the
    cost of a human glancing at a false one.

    The decision and the operator's identity are written onto the report, so the
    human-in-the-loop claim is evidenced in data rather than only in the pitch (FR-30).
    Phase 9 will additionally emit this as a process event.
    """
    report = session.get(Report, report_id)
    if report is None:
        raise NotFoundError(
            "No report exists with that id",
            code=ErrorCode.REPORT_NOT_FOUND,
            detail={"report_id": str(report_id)},
        )

    if report.status != ReportStatus.FLAGGED:
        raise ConflictError(
            "Only a flagged report can be reviewed",
            code=ErrorCode.REPORT_NOT_UNDER_REVIEW,
            detail={"report_id": str(report_id), "status": report.status.value},
        )

    verified = payload.decision == ReviewDecision.VERIFY
    report.status = ReportStatus.VERIFIED if verified else ReportStatus.REJECTED

    # Weight 0: this records who decided, without moving the computed score.
    entry = reason(
        "HUMAN_REVIEW_VERIFIED" if verified else "HUMAN_REVIEW_REJECTED",
        0,
        f"operator:{payload.reviewer}",
    )
    if payload.note:
        entry["note"] = payload.note
    report.authenticity_reasons = [*report.authenticity_reasons, entry]
    session.add(report)

    # A report an operator has just cleared belongs in the queue immediately, not on
    # the next background sweep.
    emit_event(
        session,
        case_id=report.id,
        activity=Activity.REPORT_VERIFIED if verified else Activity.REPORT_REJECTED,
        resource=f"operator:{payload.reviewer}",
        metadata={"decision": payload.decision.value, "note": payload.note},
    )

    if verified:
        enqueue_verified(session, report)

    session.commit()
    session.refresh(report)

    logger.info(
        "flagged report reviewed",
        extra={
            "report_id": str(report_id),
            "decision": payload.decision.value,
            "reviewer": payload.reviewer,
            "status": report.status.value,
        },
    )

    return ReviewResult(
        id=report.id,
        status=report.status,
        decision=payload.decision,
        reviewer=payload.reviewer,
        note=payload.note,
        authenticity_score=report.authenticity_score,
        authenticity_reasons=report.authenticity_reasons,
    )


@router.get(
    "/{report_id}",
    response_model=ReportRead,
    summary="Report detail with scores and reasons",
    responses={404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
)
def get_report(
    report_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> ReportRead:
    report = session.get(Report, report_id)
    if report is None:
        raise NotFoundError(
            "No report exists with that id",
            code=ErrorCode.REPORT_NOT_FOUND,
            detail={"report_id": str(report_id)},
        )
    return _detail(report)
