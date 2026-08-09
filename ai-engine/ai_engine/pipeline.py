"""
TriagePipeline — unified Phase 4-6 pipeline.
Extends TriageService with image analysis, fusion, and authenticity.

Architecture:
    IncidentAIInput
        ↓
    TriagePipeline
        ├── Classifier         (Granite → rule-based fallback)
        ├── Risk Extractor     (Granite → rule-based fallback)
        ├── Severity Engine    (deterministic maths)
        ├── Image Analyzer     (VisionProvider → graceful fallback)
        ├── Fusion Engine      (text + image severity fusion)
        └── Authenticity Engine (deterministic trust scoring)
        ↓
    IncidentAIOutput  (complete result with all phases)
"""

import logging
from typing import Optional

from ai_engine.authenticity.authenticity_engine import AuthenticityEngine
from ai_engine.authenticity.movement_check import PreviousReport
from ai_engine.authenticity.image_duplicate import ImageHashService
from ai_engine.classification.classifier import classify
from ai_engine.classification.risk_extractor import extract_risk_factors_rule_based
from ai_engine.exceptions import AITimeoutError, AIProviderError, SeverityCalculationError
from ai_engine.fusion.fusion_engine import FusionEngine
from ai_engine.providers.base import AIProvider
from ai_engine.providers.vision_base import VisionProvider
from ai_engine.severity.engine import calculate_severity
from ai_engine.triage_service import TriageService
from ai_engine.vision.image_analyzer import ImageAnalyzer
from ai_engine.vision.preprocessing import decode_image
from shared.schemas.classification import FallbackState, ScoringProvider
from shared.schemas.incident_ai import FullAnalysisResult, IncidentAIInput, IncidentAIOutput

logger = logging.getLogger(__name__)


class TriagePipeline:
    """
    Full Phase 4-6 triage pipeline.
    Runs classification → risk → severity → image analysis → fusion → authenticity.
    """

    def __init__(
        self,
        provider: Optional[AIProvider] = None,
        vision_provider: Optional[VisionProvider] = None,
        authenticity_engine: Optional[AuthenticityEngine] = None,
        fusion_engine: Optional[FusionEngine] = None,
        image_analyzer: Optional[ImageAnalyzer] = None,
    ) -> None:
        self._triage_service = TriageService(provider=provider)
        self._image_analyzer = image_analyzer or ImageAnalyzer(
            vision_provider=vision_provider
        )
        self._fusion_engine = fusion_engine or FusionEngine()
        self._authenticity_engine = authenticity_engine or AuthenticityEngine()

    async def run(self, incident: IncidentAIInput) -> IncidentAIOutput:
        """
        Run the full triage pipeline and return an IncidentAIOutput
        with all Phase 4-5 fields populated.

        Args:
            incident: Validated IncidentAIInput.

        Returns:
            IncidentAIOutput — never raises; degrades gracefully.
        """
        logger.info(
            "TriagePipeline started: report_id=%s has_image=%s",
            incident.report_id,
            incident.image is not None,
        )

        # ── Step 1-3: Text classification + risk + base severity ──────────────
        base_result = await self._triage_service.triage(incident)

        # ── Step 4: Image analysis (if image provided) ────────────────────────
        image_analysis = None
        if incident.image is not None:
            try:
                raw_bytes = decode_image(incident.image)
                image_analysis = await self._image_analyzer.analyze(
                    raw_bytes, description=incident.description
                )
            except Exception as exc:
                logger.warning("Image decode/analysis failed: %s", exc)
                from ai_engine.vision.schemas import ImageAnalysisResult
                image_analysis = ImageAnalysisResult.failed(str(exc))

        # ── Step 5: Fusion ────────────────────────────────────────────────────
        fused = self._fusion_engine.fuse(
            text_severity_score=base_result.severity_score,
            text_reason_codes=list(base_result.severity_reason_codes),
            image_analysis=image_analysis,
            classification_confidence=base_result.classification_confidence,
        )

        # ── Step 6: Authenticity ──────────────────────────────────────────────
        image_bytes: Optional[bytes] = None
        if incident.image is not None:
            try:
                image_bytes = decode_image(incident.image)
            except Exception:
                pass

        previous_reports: list[PreviousReport] = []  # In-memory; Person 2 will inject

        authenticity = self._authenticity_engine.calculate_authenticity(
            report_id=incident.report_id,
            reporter_pseudonym=incident.reporter_pseudonym,
            lat=incident.latitude,
            lon=incident.longitude,
            client_ts=incident.client_timestamp,
            image_bytes=image_bytes,
            previous_reports=previous_reports,
        )

        # ── Assemble final output ─────────────────────────────────────────────
        return IncidentAIOutput(
            report_id=base_result.report_id,
            incident_type=base_result.incident_type,
            classification_confidence=base_result.classification_confidence,
            classification_reason_codes=base_result.classification_reason_codes,
            risk_factors=base_result.risk_factors,
            severity_score=fused.severity_score,
            severity_label=fused.severity_label,
            severity_reason_codes=fused.severity_reason_codes,
            scoring_provider=base_result.scoring_provider,
            fallback_state=base_result.fallback_state,
            image_analysis=image_analysis,
            multimodal_mode=fused.mode,
            authenticity=authenticity,
        )

    async def run_full(self, incident: IncidentAIInput) -> FullAnalysisResult:
        """
        Run the full pipeline and return a FullAnalysisResult for /ai/analyze.

        Returns:
            FullAnalysisResult with all pipeline outputs.
        """
        result = await self.run(incident)
        return FullAnalysisResult(
            report_id=result.report_id,
            incident_type=result.incident_type,
            classification_confidence=result.classification_confidence,
            classification_reason_codes=result.classification_reason_codes,
            risk_factors=result.risk_factors,
            severity_score=result.severity_score,
            severity_label=result.severity_label,
            severity_reason_codes=result.severity_reason_codes,
            scoring_provider=result.scoring_provider,
            fallback_state=result.fallback_state,
            image_analysis=result.image_analysis,
            multimodal_mode=result.multimodal_mode,
            authenticity=result.authenticity,
            pipeline_version="4.0",
        )
