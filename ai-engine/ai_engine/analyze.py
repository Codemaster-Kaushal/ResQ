"""
RescueNet AI — Phase 8: Master Analysis Pipeline
================================================
Orchestrates report classification, severity scoring, text + image fusion,
authenticity validation, and verification status determination.
"""

import logging
import os
import tempfile
import time
from typing import Optional

from ai_engine.classification.classifier import classify
from ai_engine.classification.risk_extractor import extract_risk_factors_rule_based
from ai_engine.config import (
    SEVERITY_CRITICAL_THRESHOLD,
    SEVERITY_HIGH_THRESHOLD,
    SEVERITY_MEDIUM_THRESHOLD,
    AUTHENTICITY_VERIFIED_THRESHOLD,
    AUTHENTICITY_LIKELY_VALID_THRESHOLD,
    AUTHENTICITY_REVIEW_THRESHOLD,
    CORROBORATION_RADIUS_METERS,
    CORROBORATION_TIME_WINDOW_MINUTES,
    MAX_PLAUSIBLE_SPEED_KMH,
    IMAGE_DUPLICATE_HASH_DISTANCE,
    MAX_CLOCK_SKEW_MINUTES,
)
from ai_engine.fusion.fusion_engine import FusionEngine
from ai_engine.severity.engine import calculate_severity
from ai_engine.vision.image_analyzer import ImageAnalyzer
from ai_engine.vision.preprocessing import decode_image
from shared.schemas.severity import SeverityLabel

# Standalone authenticity module
from authenticity import analyze_authenticity

# Application dependencies
from app.dependencies import get_granite_provider, get_vision_provider
from ai_engine.providers.granite_local import GraniteLocalProvider
from ai_engine.providers.vision_granite import GraniteVisionProvider

logger = logging.getLogger(__name__)


async def analyze_report(
    report: dict,
    provider: Optional[GraniteLocalProvider] = None,
    vision_provider: Optional[GraniteVisionProvider] = None,
) -> dict:
    """
    Run the master analysis pipeline on a disaster report.

    Args:
        report: Dictionary containing:
            - report_id (str)
            - description (str)
            - image (str/None, base64-encoded)
            - latitude (float/None)
            - longitude (float/None)
            - client_timestamp (str/datetime/None)
            - reporter_pseudonym (str/None)
        provider: Optional Granite text provider. If None, resolves to default.
        vision_provider: Optional vision provider. If None, resolves to default.

    Returns:
        Dictionary matching the frozen response contract.
    """
    t0 = time.perf_counter()

    report_id = report.get("report_id")
    description = report.get("description", "")
    image_base64 = report.get("image")
    latitude = report.get("latitude")
    longitude = report.get("longitude")
    client_timestamp = report.get("client_timestamp")
    server_timestamp = report.get("server_timestamp")
    reporter_pseudonym = report.get("reporter_pseudonym")

    if not report_id or not report_id.strip():
        raise ValueError("report_id must not be empty")
    if not description or not description.strip():
        raise ValueError("description must not be empty")

    logger.info("analyze_report: report_id=%s starting master analysis pipeline", report_id)

    if provider is None:
        provider = get_granite_provider()
    if vision_provider is None:
        vision_provider = get_vision_provider()

    # 1. Classify incident using existing classifier (Target: exactly 1 Granite call)
    classification = await classify(description, provider)

    # 2. Run existing severity engine (using rule-based risk extractor to keep Granite calls to 1)
    risk_factors = extract_risk_factors_rule_based(description)
    base_severity = calculate_severity(
        risk=risk_factors,
        classification_confidence=classification.confidence,
    )

    # 3. Preserve the existing text + image/Gemma vision result
    image_analysis = None
    image_analyzer = ImageAnalyzer(vision_provider=vision_provider)
    fusion_engine = FusionEngine()

    if image_base64 is not None:
        try:
            raw_bytes = decode_image(image_base64)
            image_analysis = await image_analyzer.analyze(
                raw_bytes, description=description
            )
        except Exception as exc:
            logger.warning("Image decoding or vision analysis failed: %s", exc)
            from ai_engine.vision.schemas import ImageAnalysisResult
            image_analysis = ImageAnalysisResult.failed(str(exc))

    fused = fusion_engine.fuse(
        text_severity_score=base_severity.severity_score,
        text_reason_codes=list(base_severity.severity_reason_codes),
        image_analysis=image_analysis,
        classification_confidence=classification.confidence,
    )

    # 4. Run authenticity.py (using a temporary file to decode the base64 image if present)
    temp_img_path = None
    if image_base64 is not None:
        try:
            raw_bytes = decode_image(image_base64)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
                temp_file.write(raw_bytes)
                temp_img_path = temp_file.name
        except Exception as exc:
            logger.warning("Failed to save temporary image file for authenticity: %s", exc)

    try:
        incident_type_str = (
            classification.incident_type.value
            if hasattr(classification.incident_type, "value")
            else str(classification.incident_type)
        )
        auth_res = analyze_authenticity(
            report_id=report_id,
            image_path=temp_img_path,
            lat=latitude,
            lon=longitude,
            client_ts=client_timestamp,
            server_ts=server_timestamp,
            pseudonym=reporter_pseudonym,
            incident_type=incident_type_str,
        )
    except Exception as exc:
        logger.error("authenticity check failed: %s", exc, exc_info=True)
        auth_res = {
            "score": 50,
            "band": "NEEDS_REVIEW",
            "reason_codes": ["AUTHENTICITY_ENGINE_ERROR"],
        }
    finally:
        if temp_img_path and os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
            except Exception as exc:
                logger.warning("Failed to clean up temporary image file %s: %s", temp_img_path, exc)

    # 5. Determine verification_status
    auth_band = auth_res.get("band", "NEEDS_REVIEW")
    auth_reason_codes = list(auth_res.get("reason_codes", []))

    # Safety override rules:
    # If severity = CRITICAL (score >= 80) and authenticity = NEEDS_REVIEW (band == "NEEDS_REVIEW")
    # then verification_status = LIKELY_VALID and add CRITICAL_SEVERITY_OVERRIDE to reason_codes
    # FLAGGED is never overridden
    verification_status = auth_band
    is_critical = (
        fused.severity_score >= SEVERITY_CRITICAL_THRESHOLD
        or fused.severity_label == SeverityLabel.CRITICAL
    )
    if is_critical and auth_band == "NEEDS_REVIEW":
        verification_status = "LIKELY_VALID"
        if "CRITICAL_SEVERITY_OVERRIDE" not in auth_reason_codes:
            auth_reason_codes.append("CRITICAL_SEVERITY_OVERRIDE")

    # 6. Generate provenance
    vision_active = image_analysis is not None and image_analysis.vision_available
    triage_provider_str = (
        classification.provider.value
        if hasattr(classification.provider, "value")
        else str(classification.provider)
    )
    fallback_state_str = (
        classification.fallback_state.value
        if hasattr(classification.fallback_state, "value")
        else str(classification.fallback_state)
    )
    provenance = {
        "triage_provider": triage_provider_str,
        "triage_model": provider.model_name,
        "vision_provider": "local_gemma" if vision_active else "none",
        "vision_model": vision_provider.model_name if vision_active else "none",
        "scoring_provider": "hybrid",
        "fallback_state": fallback_state_str,
        "external_api_calls": False,
    }

    # Generate thresholds block
    thresholds = {
        "severity_critical": SEVERITY_CRITICAL_THRESHOLD,
        "severity_high": SEVERITY_HIGH_THRESHOLD,
        "severity_medium": SEVERITY_MEDIUM_THRESHOLD,
        "authenticity_verified": AUTHENTICITY_VERIFIED_THRESHOLD,
        "authenticity_likely_valid": AUTHENTICITY_LIKELY_VALID_THRESHOLD,
        "authenticity_review": AUTHENTICITY_REVIEW_THRESHOLD,
        "phash_duplicate_distance": IMAGE_DUPLICATE_HASH_DISTANCE,
        "timestamp_plausibility_minutes": MAX_CLOCK_SKEW_MINUTES,
        "corroboration_distance_meters": CORROBORATION_RADIUS_METERS,
        "corroboration_time_window_minutes": CORROBORATION_TIME_WINDOW_MINUTES,
        "impossible_movement_speed_kmh": MAX_PLAUSIBLE_SPEED_KMH,
    }

    # 7. Measure total latency
    latency_ms = int((time.perf_counter() - t0) * 1000)

    # 8. Compile the final response contract
    incident_type_str = (
        classification.incident_type.value
        if hasattr(classification.incident_type, "value")
        else str(classification.incident_type)
    )
    fused_label_str = (
        fused.severity_label.value
        if hasattr(fused.severity_label, "value")
        else str(fused.severity_label)
    )
    fused_reason_codes = [
        code.value if hasattr(code, "value") else str(code)
        for code in fused.severity_reason_codes
    ]

    multimodal_mode = "TEXT_AND_IMAGE" if image_base64 is not None and image_analysis is not None and image_analysis.vision_available else "TEXT_ONLY"
    result = {
        "report_id": report_id,
        "incident_type": incident_type_str,
        "severity": {
            "score": fused.severity_score,
            "band": fused_label_str,
            "reason_codes": fused_reason_codes,
        },
        "authenticity": {
            "score": auth_res.get("score", 50),
            "reason_codes": auth_reason_codes,
        },
        "verification_status": verification_status,
        "confidence": classification.confidence,
        "provenance": provenance,
        "thresholds": thresholds,
        "latency_ms": latency_ms,
        # Compatibility aliases for legacy tests and downstream integrations
        "severity_score": fused.severity_score,
        "severity_label": fused_label_str,
        "severity_reason_codes": fused_reason_codes,
        "scoring_provider": "hybrid",
        "fallback_state": fallback_state_str,
        "multimodal_mode": multimodal_mode,
    }
    return result
