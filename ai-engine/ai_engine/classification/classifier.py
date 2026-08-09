"""
Incident classifier — combines Granite AI and rule-based fallback.

Precedence rule for overlapping types (documented for Person 2):
  When a description mentions BOTH flooding AND trapped persons, the classifier
  returns whichever has the stronger signal from the AI or keyword weights.
  Classification precedence is NOT hardcoded; it falls naturally from Granite's
  NLU or the rule-based weight accumulation. This produces sensible results
  for mixed incidents without losing detail (risk factors capture both).
"""

import logging
from typing import Optional

from ai_engine.classification.rule_based import classify_rule_based
from ai_engine.exceptions import AITimeoutError, AIProviderError
from ai_engine.providers.base import AIProvider
from shared.schemas.classification import ClassificationResult, FallbackState, ScoringProvider

logger = logging.getLogger(__name__)


async def classify(
    description: str,
    provider: Optional[AIProvider] = None,
) -> ClassificationResult:
    """
    Classify the incident type from free text.

    Strategy:
    1. Attempt Granite classification via the given provider.
    2. On timeout → rule-based fallback, fallback_state = AI_BACKFILL_PENDING.
    3. On provider unavailable or Pydantic validation failure → rule-based fallback,
       fallback_state = RULE_BASED.
    4. If no provider is given → rule-based directly.

    Args:
        description: Free-text incident description.
        provider: Optional AIProvider (GraniteLocalProvider). If None, rule-based is used.

    Returns:
        ClassificationResult — never raises (except truly unexpected errors).
    """
    if provider is None:
        logger.info("No AI provider configured — using rule-based classifier directly.")
        return classify_rule_based(description)

    try:
        result = await provider.classify_incident(description)
        logger.info(
            "Granite classification: type=%s confidence=%.2f",
            result.incident_type,
            result.confidence,
        )
        return result

    except AITimeoutError:
        logger.warning(
            "Granite classification timed out — falling back to rule-based (AI_BACKFILL_PENDING)."
        )
        fallback = classify_rule_based(description)
        # Override state to indicate AI can backfill later
        return fallback.model_copy(
            update={
                "fallback_state": FallbackState.AI_BACKFILL_PENDING,
                "provider": ScoringProvider.RULE_BASED,
            }
        )

    except AIProviderError as exc:
        logger.warning(
            "Granite classification failed (%s) — falling back to rule-based.", exc.code
        )
        fallback = classify_rule_based(description)
        return fallback.model_copy(
            update={
                "fallback_state": FallbackState.RULE_BASED,
                "provider": ScoringProvider.RULE_BASED,
            }
        )
