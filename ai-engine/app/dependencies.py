"""
Dependency injection for FastAPI routes.
Providers and services are created once and reused across requests.
"""

import logging
from functools import lru_cache

from ai_engine.config import (
    AI_TIMEOUT_SECONDS,
    GRANITE_MODEL,
    OLLAMA_HOST,
    VISION_MODEL,
    VISION_TIMEOUT_SECONDS,
)
from ai_engine.pipeline import TriagePipeline
from ai_engine.providers.granite_local import GraniteLocalProvider
from ai_engine.providers.vision_granite import GraniteVisionProvider
from ai_engine.triage_service import TriageService

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_granite_provider() -> GraniteLocalProvider:
    """Singleton Granite text provider — instantiated once per process."""
    logger.info(
        "Initialising GraniteLocalProvider: model=%s host=%s timeout=%.1fs",
        GRANITE_MODEL,
        OLLAMA_HOST,
        AI_TIMEOUT_SECONDS,
    )
    return GraniteLocalProvider(
        model=GRANITE_MODEL,
        ollama_host=OLLAMA_HOST,
        timeout=AI_TIMEOUT_SECONDS,
    )


@lru_cache(maxsize=1)
def get_vision_provider() -> GraniteVisionProvider:
    """
    Singleton vision provider — uses Gemma 4 (or any configured vision model).
    The provider auto-detects at first use whether the model actually supports vision.
    """
    logger.info(
        "Initialising GraniteVisionProvider: preferred_model=%s host=%s",
        VISION_MODEL,
        OLLAMA_HOST,
    )
    return GraniteVisionProvider(
        ollama_host=OLLAMA_HOST,
        timeout=VISION_TIMEOUT_SECONDS,
        preferred_model=VISION_MODEL,
    )


@lru_cache(maxsize=1)
def get_triage_pipeline() -> TriagePipeline:
    """
    Singleton TriagePipeline with both text (Granite) and vision (Gemma 4) providers.
    This is the primary dependency for /ai/analyze and /ai/triage.
    """
    return TriagePipeline(
        provider=get_granite_provider(),
        vision_provider=get_vision_provider(),
    )


def get_triage_service() -> TriageService:
    """Create a TriageService backed by the singleton Granite provider (Phases 0-3 compat)."""
    return TriageService(provider=get_granite_provider())
