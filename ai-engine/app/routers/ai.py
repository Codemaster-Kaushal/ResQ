"""
AI inference endpoints — classify, triage, severity, provenance, analyze.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ai_engine.classification.classifier import classify
from ai_engine.classification.risk_extractor import extract_risk_factors_rule_based
from ai_engine.analyze import analyze_report
from ai_engine.config import (
    GRANITE_MODEL,
    GRANITE_MODEL_VERSION,
    IMAGE_FUSION_WEIGHT,
    TEXT_FUSION_WEIGHT,
    OLLAMA_HOST,
    VISION_MODEL,
    VISION_PROVIDER,
    AUTHENTICITY_REVIEW_THRESHOLD,
    AUTHENTICITY_LIKELY_VALID_THRESHOLD,
    AUTHENTICITY_VERIFIED_THRESHOLD,
    MAX_IMAGE_SIZE_MB,
    CORROBORATION_RADIUS_METERS,
    CORROBORATION_TIME_WINDOW_MINUTES,
    MAX_CLOCK_SKEW_MINUTES,
)
from ai_engine.exceptions import (
    AIEngineError,
    AIModelUnavailableError,
    AITimeoutError,
    ClassificationError,
    SeverityCalculationError,
)
from provenance import error_response, get_provenance, get_thresholds
from ai_engine.pipeline import TriagePipeline
from ai_engine.providers.granite_local import GraniteLocalProvider
from ai_engine.severity.config import CRITICAL_THRESHOLD, HIGH_THRESHOLD, MEDIUM_THRESHOLD
from ai_engine.severity.engine import calculate_severity
from ai_engine.triage_service import TriageService
from app.dependencies import get_granite_provider, get_triage_pipeline, get_triage_service, get_vision_provider
from shared.schemas.classification import FallbackState, ScoringProvider
from shared.schemas.incident_ai import (
    AIErrorDetail,
    AIErrorResponse,
    FullAnalysisResult,
    IncidentAIInput,
    IncidentAIOutput,
)

router = APIRouter(prefix="/ai", tags=["ai"])
logger = logging.getLogger(__name__)


# ── Request/response models for slim endpoints ────────────────────────────────

class ClassifyRequest(BaseModel):
    report_id: str = Field(..., description="Unique report ID")
    description: str = Field(..., description="Free-text incident description")

    @field_validator("report_id")
    @classmethod
    def report_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("report_id must not be empty")
        return v.strip()

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description must not be empty")
        return v.strip()


class ClassifyResponse(BaseModel):
    report_id: str
    incident_type: str
    confidence: float
    classification_reason_codes: list[str]
    provider: str
    fallback_state: str


class SeverityRequest(BaseModel):
    report_id: str = Field(..., description="Unique report ID")
    description: str = Field(..., description="Incident description")
    image: Optional[str] = None  # FR-9: optional

    @field_validator("report_id")
    @classmethod
    def report_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("report_id must not be empty")
        return v.strip()

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description must not be empty")
        return v.strip()


class SeverityResponse(BaseModel):
    report_id: str
    severity_score: int
    severity_label: str
    severity_reason_codes: list[str]
    risk_factors: dict
    scoring_provider: str
    fallback_state: str


# ── Error helper ──────────────────────────────────────────────────────────────

def _ai_error(exc: Exception, status_code: int = 503, *, fallback: str = "rule_based") -> HTTPException:
    error_code = getattr(exc, "code", "UNKNOWN_ERROR")
    message = getattr(exc, "message", str(exc))
    if isinstance(exc, AIModelUnavailableError):
        error_code = "MODEL_UNAVAILABLE"
        message = "Local AI model unavailable"
    elif isinstance(exc, AITimeoutError):
        error_code = "AI_TIMEOUT"
        message = "Local AI request timed out"
    elif isinstance(exc, ValueError):
        lower = str(exc).lower()
        if "image" in lower:
            error_code = "INVALID_IMAGE"
            message = "Invalid image payload"
        else:
            error_code = "INVALID_METADATA"
            message = "Invalid report metadata"

    payload = error_response(
        error_code,
        message,
        fallback=fallback,
        retryable=getattr(exc, "retryable", False),
    )
    return HTTPException(status_code=status_code, detail=payload)


# ── Pipeline dependency (singleton from dependencies.py, includes vision) ────

# get_triage_pipeline is imported from app.dependencies — see dependencies.py


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/classify", response_model=ClassifyResponse, summary="Classify incident type (FR-6)")
async def classify_incident(
    request: ClassifyRequest,
    provider: GraniteLocalProvider = Depends(get_granite_provider),
):
    """
    Classify the incident type from a free-text description.
    Uses IBM Granite if available; falls back to rule-based automatically.
    """
    logger.info("POST /ai/classify report_id=%s", request.report_id)
    try:
        result = await classify(request.description, provider)
    except AIEngineError as exc:
        raise _ai_error(exc, fallback="rule_based")
    except Exception as exc:
        logger.error("Unexpected error in /ai/classify: %s", exc, exc_info=True)
        raise _ai_error(exc, status_code=500, fallback="rule_based")

    return ClassifyResponse(
        report_id=request.report_id,
        incident_type=result.incident_type.value,
        confidence=result.confidence,
        classification_reason_codes=[c.value for c in result.reason_codes],
        provider=result.provider.value,
        fallback_state=result.fallback_state.value,
    )


@router.post("/triage", response_model=IncidentAIOutput, summary="Full triage (classify + risk + severity)")
async def triage_incident(
    incident: IncidentAIInput,
    service: TriageService = Depends(get_triage_service),
):
    """
    Full triage pipeline: classification → risk extraction → severity scoring.
    Image input is accepted but not yet processed (FR-9 architecture support).
    """
    logger.info("POST /ai/triage report_id=%s", incident.report_id)
    try:
        result = await service.triage(incident)
    except AIEngineError as exc:
        raise _ai_error(exc)
    except Exception as exc:
        logger.error("Unexpected error in /ai/triage: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=AIErrorResponse(
                error=AIErrorDetail(code="INTERNAL_ERROR", message="An unexpected error occurred.", retryable=False)
            ).model_dump(),
        )
    return result


@router.post("/severity", response_model=SeverityResponse, summary="Severity score only (FR-7 + FR-8)")
async def severity_only(
    request: SeverityRequest,
    provider: GraniteLocalProvider = Depends(get_granite_provider),
):
    """
    Extract risk factors and compute severity score without full triage.
    Useful for re-scoring or partial updates.
    """
    logger.info("POST /ai/severity report_id=%s", request.report_id)
    try:
        # Determine fallback state for risk extraction
        from shared.schemas.classification import FallbackState
        available = await provider.is_available()
        if available:
            try:
                risk = await provider.extract_risk_factors(request.description)
                scoring_provider = ScoringProvider.LOCAL_GRANITE
                fallback_state = FallbackState.NORMAL
            except Exception as exc:
                logger.warning("Granite risk extraction failed in /ai/severity: %s", exc)
                risk = extract_risk_factors_rule_based(request.description)
                scoring_provider = ScoringProvider.RULE_BASED
                fallback_state = FallbackState.RULE_BASED
        else:
            risk = extract_risk_factors_rule_based(request.description)
            scoring_provider = ScoringProvider.RULE_BASED
            fallback_state = FallbackState.AI_UNAVAILABLE

        severity = calculate_severity(risk)

    except AIEngineError as exc:
        raise _ai_error(exc)
    except Exception as exc:
        logger.error("Unexpected error in /ai/severity: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=AIErrorResponse(
                error=AIErrorDetail(code="INTERNAL_ERROR", message="An unexpected error occurred.", retryable=False)
            ).model_dump(),
        )

    return SeverityResponse(
        report_id=request.report_id,
        severity_score=severity.severity_score,
        severity_label=severity.severity_label.value,
        severity_reason_codes=[c.value for c in severity.severity_reason_codes],
        risk_factors=risk.model_dump(),
        scoring_provider=scoring_provider.value,
        fallback_state=fallback_state.value,
    )


@router.post(
    "/analyze",
    summary="Full multimodal + authenticity analysis (Phase 8)",
)
async def analyze_incident(
    incident: IncidentAIInput,
    provider = Depends(get_granite_provider),
    vision_provider = Depends(get_vision_provider),
):
    """
    Primary Phase 8 integration endpoint.
    Runs the full master pipeline using analyze_report and returns the frozen response contract.
    """
    logger.info("POST /ai/analyze report_id=%s has_image=%s", incident.report_id, incident.image is not None)
    try:
        report_dict = incident.model_dump()
        result = await analyze_report(report_dict, provider=provider, vision_provider=vision_provider)
    except ValueError as exc:
        logger.warning("Validation error in /ai/analyze: %s", exc)
        raise _ai_error(exc, status_code=422, fallback="rule_based")
    except AIEngineError as exc:
        raise _ai_error(exc, fallback="rule_based")
    except Exception as exc:
        logger.error("Unexpected error in /ai/analyze: %s", exc, exc_info=True)
        raise _ai_error(exc, status_code=500, fallback="rule_based")
    return result


@router.post(
    "/authenticity",
    summary="Standalone authenticity check (Phase 5)",
)
async def check_authenticity(incident: IncidentAIInput):
    """
    Standalone authenticity check — runs only the trust engine without full triage.
    Useful for re-evaluating existing reports.
    """
    from ai_engine.authenticity.authenticity_engine import AuthenticityEngine
    from ai_engine.vision.preprocessing import decode_image as _decode

    logger.info("POST /ai/authenticity report_id=%s", incident.report_id)

    image_bytes: Optional[bytes] = None
    if incident.image is not None:
        try:
            image_bytes = _decode(incident.image)
        except Exception as exc:
            logger.warning("Image decode for authenticity check failed: %s", exc)

    engine = AuthenticityEngine()
    result = engine.calculate_authenticity(
        report_id=incident.report_id,
        reporter_pseudonym=incident.reporter_pseudonym,
        lat=incident.latitude,
        lon=incident.longitude,
        client_ts=incident.client_timestamp,
        image_bytes=image_bytes,
    )
    return result.model_dump()


@router.get("/provenance", summary="Full model and scoring provenance (Phase 9)")
async def provenance(provider: GraniteLocalProvider = Depends(get_granite_provider)):
    """Return the exact model provenance and threshold metadata for the offline pipeline."""
    from ai_engine.providers.vision_granite import GraniteVisionProvider

    vision_provider = GraniteVisionProvider(
        ollama_host=OLLAMA_HOST,
        preferred_model=VISION_MODEL,
    )
    vision_available = await vision_provider.is_available()

    provenance_record = get_provenance(scoring_provider="hybrid", fallback_state="NORMAL")
    provenance_record["triage_model"] = provider.model_name
    provenance_record["model"] = provider.model_name
    provenance_record["model_version"] = provider.model_version
    provenance_record["vision_model"] = VISION_MODEL if vision_available else "gemma4:latest"
    provenance_record["vision_provider"] = "local_gemma"
    provenance_record["thresholds"] = {
        "critical": 80,
        "high": 60,
        "medium": 40,
        "authenticity_verified": AUTHENTICITY_VERIFIED_THRESHOLD,
        "authenticity_likely_valid": AUTHENTICITY_LIKELY_VALID_THRESHOLD,
        "authenticity_review": AUTHENTICITY_REVIEW_THRESHOLD,
    }
    provenance_record["vision_available"] = vision_available
    provenance_record["fusion"] = {
        "text_weight": TEXT_FUSION_WEIGHT,
        "image_weight": IMAGE_FUSION_WEIGHT,
    }
    provenance_record["authenticity"] = {
        "corroboration_radius_meters": CORROBORATION_RADIUS_METERS,
        "corroboration_time_window_minutes": CORROBORATION_TIME_WINDOW_MINUTES,
        "max_clock_skew_minutes": MAX_CLOCK_SKEW_MINUTES,
        "max_image_size_mb": MAX_IMAGE_SIZE_MB,
    }
    provenance_record["pipeline_version"] = "4.0"
    provenance_record["phases"] = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
    provenance_record["offline_capable"] = True
    provenance_record["fallback_enabled"] = True
    return provenance_record
