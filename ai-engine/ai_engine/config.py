"""
RescueNet AI Engine — centralized configuration.
All environment variables and defaults live here.
Never hard-code model names or endpoints elsewhere.
"""

import os
from typing import Optional


# ── Ollama / Granite ──────────────────────────────────────────────────────────

GRANITE_MODEL: str = os.environ.get("GRANITE_MODEL", "granite3.3:8b")
"""
IBM Granite model tag as known to Ollama.
Override with: GRANITE_MODEL=granite3.1:2b  (or any pulled tag)
"""

GRANITE_MODEL_VERSION: str = os.environ.get("GRANITE_MODEL_VERSION", "latest")
"""Human-readable version label returned by /ai/provenance."""

OLLAMA_HOST: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
"""Base URL of the local Ollama server."""

AI_TIMEOUT_SECONDS: float = float(os.environ.get("AI_TIMEOUT_SECONDS", "5"))
"""
Hard timeout for text AI inference (FR-10).
If exceeded the rule-based fallback is activated automatically.
"""

VISION_TIMEOUT_SECONDS: float = float(os.environ.get("VISION_TIMEOUT_SECONDS", "120"))
"""
Hard timeout for vision/image inference.
Image models (e.g. Gemma 4 on CPU) need significantly more time than text models.
Default: 120s. Reduce on GPU hardware.  Override with: VISION_TIMEOUT_SECONDS=30
"""

AI_TEMPERATURE: float = float(os.environ.get("AI_TEMPERATURE", "0.0"))
"""
Inference temperature — keep at 0 for deterministic classification.
"""

# ── Severity thresholds ───────────────────────────────────────────────────────

SEVERITY_CRITICAL_THRESHOLD: int = int(os.environ.get("SEVERITY_CRITICAL_THRESHOLD", "80"))
SEVERITY_HIGH_THRESHOLD: int = int(os.environ.get("SEVERITY_HIGH_THRESHOLD", "60"))
SEVERITY_MEDIUM_THRESHOLD: int = int(os.environ.get("SEVERITY_MEDIUM_THRESHOLD", "40"))

# ── Application ───────────────────────────────────────────────────────────────

APP_LOG_LEVEL: str = os.environ.get("APP_LOG_LEVEL", "INFO")
APP_ENV: str = os.environ.get("APP_ENV", "development")

# ── Phase 4: Vision / Multimodal ─────────────────────────────────────────────

VISION_MODEL: str = os.environ.get("VISION_MODEL", "gemma4:latest")
"""
Vision model tag for image analysis (FR-9 / Phase 4).
Default: gemma4:latest (8B multimodal — vision+audio+tools, 128K context, Apache 2.0).
Override with: VISION_MODEL=qwen2.5vl:7b  (pull first with: ollama pull qwen2.5vl:7b)
Set to empty string "" to fall back to auto-detect from installed Ollama models.
"""

VISION_PROVIDER: str = os.environ.get("VISION_PROVIDER", "granite_vision")
"""Vision provider name."""

TEXT_FUSION_WEIGHT: float = float(os.environ.get("TEXT_FUSION_WEIGHT", "0.60"))
"""Weight assigned to text analysis in fusion (0-1)."""

IMAGE_FUSION_WEIGHT: float = float(os.environ.get("IMAGE_FUSION_WEIGHT", "0.40"))
"""Weight assigned to image analysis in fusion (0-1)."""

MAX_IMAGE_SIZE_MB: float = float(os.environ.get("MAX_IMAGE_SIZE_MB", "10.0"))
"""Maximum accepted image size in megabytes."""

# ── Phase 5: Authenticity ────────────────────────────────────────────────────

AUTHENTICITY_VERIFIED_THRESHOLD: int = int(
    os.environ.get("AUTHENTICITY_VERIFIED_THRESHOLD", "90")
)
"""Minimum score for VERIFIED status."""

AUTHENTICITY_LIKELY_VALID_THRESHOLD: int = int(
    os.environ.get("AUTHENTICITY_LIKELY_VALID_THRESHOLD", "70")
)
"""Minimum score for LIKELY_VALID status."""

AUTHENTICITY_REVIEW_THRESHOLD: int = int(
    os.environ.get("AUTHENTICITY_REVIEW_THRESHOLD", "50")
)
"""Below this score review_required is set to True."""

CORROBORATION_RADIUS_METERS: int = int(
    os.environ.get("CORROBORATION_RADIUS_METERS", "500")
)
"""Radius in meters for corroboration search."""

CORROBORATION_TIME_WINDOW_MINUTES: int = int(
    os.environ.get("CORROBORATION_TIME_WINDOW_MINUTES", "15")
)
"""Time window in minutes for corroboration search."""

MAX_CLOCK_SKEW_MINUTES: int = int(os.environ.get("MAX_CLOCK_SKEW_MINUTES", "15"))
"""Maximum acceptable client/server timestamp difference in minutes."""

MAX_PLAUSIBLE_SPEED_KMH: float = float(
    os.environ.get("MAX_PLAUSIBLE_SPEED_KMH", "300")
)
"""Maximum plausible reporter speed in km/h (above this = flag movement)."""

IMAGE_DUPLICATE_HASH_DISTANCE: int = int(
    os.environ.get("IMAGE_DUPLICATE_HASH_DISTANCE", "8")
)
"""Hamming distance threshold for near-duplicate image detection."""

# ── Authenticity scoring weights ──────────────────────────────────────────────

IMAGE_ORIGINALITY_WEIGHT: float = float(
    os.environ.get("IMAGE_ORIGINALITY_WEIGHT", "25")
)
GEO_VALIDITY_WEIGHT: float = float(os.environ.get("GEO_VALIDITY_WEIGHT", "20"))
TIME_PLAUSIBILITY_WEIGHT: float = float(
    os.environ.get("TIME_PLAUSIBILITY_WEIGHT", "20")
)
MOVEMENT_PLAUSIBILITY_WEIGHT: float = float(
    os.environ.get("MOVEMENT_PLAUSIBILITY_WEIGHT", "15")
)
CORROBORATION_WEIGHT: float = float(os.environ.get("CORROBORATION_WEIGHT", "20"))
