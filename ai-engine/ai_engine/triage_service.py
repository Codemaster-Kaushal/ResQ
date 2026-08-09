"""
TriageService — orchestrates classification, risk extraction, and severity.

Architecture:
    IncidentAIInput
        ↓
    TriageService
        ├── Classifier  (Granite → rule-based fallback)
        ├── Risk Extractor  (Granite → rule-based fallback)
        └── Severity Engine  (deterministic maths)
        ↓
    IncidentAIOutput  (complete triage result)
"""

import logging
from typing import Optional

from ai_engine.classification.classifier import classify
from ai_engine.classification.risk_extractor import extract_risk_factors_rule_based
from ai_engine.exceptions import AITimeoutError, AIProviderError, SeverityCalculationError
from ai_engine.providers.base import AIProvider
from ai_engine.severity.engine import calculate_severity
from shared.schemas.classification import FallbackState, ScoringProvider
from shared.schemas.incident_ai import IncidentAIInput, IncidentAIOutput
from shared.schemas.severity import SeverityReasonCode

logger = logging.getLogger(__name__)


class TriageService:
    """
    Combines classification, risk-factor extraction, and severity scoring
    into a single IncidentAIOutput result.
    """

    def __init__(self, provider: Optional[AIProvider] = None) -> None:
        self._provider = provider

    async def triage(self, incident: IncidentAIInput) -> IncidentAIOutput:
        """
        Run the full triage pipeline for a single incident.

        Args:
            incident: Validated IncidentAIInput.

        Returns:
            IncidentAIOutput — never raises; degrades gracefully to rule-based.
        """
        logger.info(
            "Triage started: report_id=%s reporter=%s",
            incident.report_id,
            incident.reporter_pseudonym,
        )

        # ── Step 1: Classify ──────────────────────────────────────────────────
        classification = await classify(incident.description, self._provider)
        logger.info(
            "Classification: type=%s confidence=%.2f provider=%s fallback=%s",
            classification.incident_type,
            classification.confidence,
            classification.provider,
            classification.fallback_state,
        )

        # ── Step 2: Extract risk factors ──────────────────────────────────────
        risk_factors = await self._extract_risk(incident.description, classification.fallback_state)
        logger.info("Risk factors extracted: %s", risk_factors.model_dump())

        # ── Step 3: Calculate severity ────────────────────────────────────────
        try:
            severity = calculate_severity(
                risk=risk_factors,
                classification_confidence=classification.confidence,
            )
        except Exception as exc:
            logger.error("Severity calculation failed unexpectedly: %s", exc, exc_info=True)
            raise SeverityCalculationError(str(exc)) from exc

        logger.info(
            "Severity: score=%d label=%s codes=%s",
            severity.severity_score,
            severity.severity_label,
            [c.value for c in severity.severity_reason_codes],
        )

        # ── Step 4: Determine overall provider and fallback state ─────────────
        scoring_provider = (
            ScoringProvider.LOCAL_GRANITE
            if classification.provider == ScoringProvider.LOCAL_GRANITE
            else ScoringProvider.RULE_BASED
        )
        fallback_state = classification.fallback_state

        return IncidentAIOutput(
            report_id=incident.report_id,
            incident_type=classification.incident_type,
            classification_confidence=classification.confidence,
            classification_reason_codes=classification.reason_codes,
            risk_factors=risk_factors,
            severity_score=severity.severity_score,
            severity_label=severity.severity_label,
            severity_reason_codes=severity.severity_reason_codes,
            scoring_provider=scoring_provider,
            fallback_state=fallback_state,
        )

    async def _extract_risk(self, description: str, current_fallback: FallbackState):
        """
        Extract risk factors using Granite if available, otherwise rule-based.
        If the classification already fell back, skip Granite for risk extraction too.
        """
        if self._provider is None or current_fallback != FallbackState.NORMAL:
            logger.debug("Risk extraction: using rule-based (provider=%s fallback=%s)", self._provider, current_fallback)
            return extract_risk_factors_rule_based(description)

        try:
            return await self._provider.extract_risk_factors(description)
        except AITimeoutError:
            logger.warning("Granite risk extraction timed out — using rule-based fallback.")
            return extract_risk_factors_rule_based(description)
        except AIProviderError as exc:
            logger.warning("Granite risk extraction failed (%s) — using rule-based fallback.", exc.code)
            return extract_risk_factors_rule_based(description)
