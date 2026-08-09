"""
Health endpoints — system and AI provider status.
"""

import logging

from fastapi import APIRouter, Depends

from ai_engine.config import GRANITE_MODEL, GRANITE_MODEL_VERSION, OLLAMA_HOST, VISION_MODEL
from ai_engine.providers.granite_local import GraniteLocalProvider
from app.dependencies import get_granite_provider

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health", summary="Basic liveness check")
async def health():
    """Returns 200 OK when the application is running."""
    return {"status": "ok", "service": "RescueNet AI Engine"}


@router.get("/ai/health", summary="AI provider health check (Phase 13)")
async def health_ai_compat(provider: GraniteLocalProvider = Depends(get_granite_provider)):
    """
    Exposes health and local/offline capabilities for Phase 13 integration.
    """
    available = await provider.is_available()
    status = "ok" if available else "degraded"
    return {
        "status": status,
        "provider": provider.provider_name,
        "model": provider.model_name,
        "offline_capable": True,
        "local_capable": True,
        "configured_models": {
            "triage_model": provider.model_name,
            "vision_model": VISION_MODEL,
        }
    }


@router.get("/health/ai", summary="AI provider health check")
async def health_ai(provider: GraniteLocalProvider = Depends(get_granite_provider)):
    """
    Checks whether the local IBM Granite model is available through Ollama.

    Returns:
        200 OK with status="ok" when the model is reachable.
        200 OK with status="degraded" and error_code when unavailable.
        (Always returns 200 — the caller inspects the body for degraded state.)
    """
    available = await provider.is_available()
    if available:
        return {
            "status": "ok",
            "provider": provider.provider_name,
            "model": provider.model_name,
            "offline_capable": True,
        }
    else:
        logger.warning(
            "AI health check failed: model=%s host=%s", provider.model_name, OLLAMA_HOST
        )
        return {
            "status": "degraded",
            "provider": provider.provider_name,
            "model": provider.model_name,
            "offline_capable": True,
            "error_code": "AI_PROVIDER_UNAVAILABLE",
        }
