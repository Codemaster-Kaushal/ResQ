import os

from ai_engine.config import GRANITE_MODEL, GRANITE_MODEL_VERSION, VISION_MODEL


def get_provenance(scoring_provider: str = "hybrid", fallback_state: str = "NORMAL") -> dict:
    """Return model provenance metadata for the RescueNet AI pipeline."""
    return {
        "triage_provider": "local_granite",
        "triage_model": GRANITE_MODEL,
        "model_version": GRANITE_MODEL_VERSION or "1.0.0",
        "vision_provider": "local_gemma",
        "vision_model": VISION_MODEL or "gemma4:latest",
        "embedding_provider": "local",
        "external_api_calls": False,
        "scoring_provider": scoring_provider or "hybrid",
        "fallback_state": fallback_state or "NORMAL",
    }


def get_thresholds() -> dict:
    """Return the key scoring and safety thresholds in a compact dict."""
    return {
        "critical": 80,
        "high": 60,
        "medium": 40,
        "authenticity_review": 50,
    }


def error_response(
    code: str,
    message: str,
    *,
    fallback: str = "rule_based",
    retryable: bool = True,
) -> dict:
    """Return a structured error payload without exposing raw stack traces."""
    return {
        "error": {
            "code": code,
            "message": message,
            "fallback": fallback,
            "retryable": retryable,
        }
    }
