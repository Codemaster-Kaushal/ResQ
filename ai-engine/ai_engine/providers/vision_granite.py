"""
GraniteVisionProvider — vision analysis using Ollama multimodal models.

Strategy:
    1. Check if any available Ollama model supports vision.
    2. If yes: use that model with image bytes for analysis.
    3. If no:  return structured VISION_UNAVAILABLE response honestly.
    Never fake vision results.
"""

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from ai_engine.config import (
    AI_TEMPERATURE,
    AI_TIMEOUT_SECONDS,
    GRANITE_MODEL,
    OLLAMA_HOST,
    VISION_MODEL,
    VISION_TIMEOUT_SECONDS,
)
from ai_engine.providers.vision_base import VisionProvider
from ai_engine.vision.schemas import (
    ImageAnalysisResult,
    VisualReasonCode,
    VisualSignals,
)

logger = logging.getLogger(__name__)

# ── Vision-capable model name fragments ──────────────────────────────────────
# These tags indicate a model supports vision/image input.
# Add new model families here as they become available in Ollama.
_VISION_MODEL_TAGS = (
    "llava", "bakllava", "moondream", "vision", "minicpm-v",
    "llava-llama3", "llava-phi3", "granite3.2-vision", "granite-vision",
    # Gemma 4 (8B, multimodal — vision + audio + tools + thinking)
    "gemma4",
    # Qwen2.5-VL (7B/14B — excellent structured-output VL model)
    "qwen2.5vl", "qwen2-vl", "qwen-vl",
    # Other common vision models
    "minicpm", "internvl", "phi-3-vision", "phi3-vision",
)

# ── System prompt for vision analysis ────────────────────────────────────────
_VISION_SYSTEM = (
    "You are a disaster-response image analyst. "
    "Analyse the provided image in context of the incident description. "
    "Detect the presence of: flood water, trapped or visible people, structural damage, "
    "fire, smoke, blocked roads, submerged vehicles, unsafe environments. "
    "Reply ONLY with valid JSON matching this schema exactly: "
    '{"flood_water": <bool>, "people_visible": <bool>, "structural_damage": <bool>, '
    '"fire_present": <bool>, "smoke_visible": <bool>, "road_blocked": <bool>, '
    '"vehicle_submerged": <bool>, "unsafe_environment": <bool>, "visual_confidence": <0.0-1.0>}\n'
    "Do not include any explanation or extra text."
)


class GraniteVisionProvider(VisionProvider):
    """
    Vision provider that uses a locally running Ollama vision-capable model.
    If no vision-capable model is available, returns VISION_UNAVAILABLE honestly.
    """

    def __init__(
        self,
        ollama_host: str = OLLAMA_HOST,
        timeout: float = VISION_TIMEOUT_SECONDS,
        preferred_model: str = VISION_MODEL,
    ) -> None:
        self._ollama_host = ollama_host.rstrip("/")
        self._timeout = float(timeout)
        self._preferred_model = preferred_model
        self._client = httpx.AsyncClient(
            base_url=self._ollama_host,
            timeout=httpx.Timeout(self._timeout + 5),
        )
        self._detected_model: Optional[str] = None
        self._vision_checked: bool = False

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return "granite_vision"

    @property
    def model_name(self) -> str:
        return self._detected_model or self._preferred_model or "none"

    # ── Vision model detection ────────────────────────────────────────────────

    async def _detect_vision_model(self) -> Optional[str]:
        """
        Query Ollama for available models and return the first vision-capable one.
        If VISION_MODEL is explicitly configured and available, prefer that.
        Returns None if no vision model is found.
        """
        try:
            resp = await self._client.get("/api/tags", timeout=3.0)
            if resp.status_code != 200:
                return None
            data = resp.json()
            model_names = [m.get("name", "") for m in data.get("models", [])]
        except Exception as exc:
            logger.debug("Ollama model list failed: %s", exc)
            return None

        # If explicitly configured, check it first
        if self._preferred_model:
            for name in model_names:
                if self._preferred_model == name or self._preferred_model == name.split(":")[0]:
                    if self._is_vision_model(name):
                        logger.info("Using configured vision model: %s", name)
                        return name
            # Configured model exists but isn't vision-capable → no vision
            for name in model_names:
                if self._preferred_model == name or self._preferred_model == name.split(":")[0]:
                    logger.info(
                        "Configured model '%s' does not appear to support vision.", name
                    )
                    return None

        # Auto-detect from available models
        for name in model_names:
            if self._is_vision_model(name):
                logger.info("Auto-detected vision model: %s", name)
                return name

        logger.info("No vision-capable model found in Ollama.")
        return None

    @staticmethod
    def _is_vision_model(model_name: str) -> bool:
        """Return True if the model name suggests vision capability."""
        lower = model_name.lower()
        return any(tag in lower for tag in _VISION_MODEL_TAGS)

    # ── Health ────────────────────────────────────────────────────────────────

    async def is_available(self) -> bool:
        """Return True if a vision-capable model is available."""
        model = await self._detect_vision_model()
        return model is not None

    # ── Image analysis ────────────────────────────────────────────────────────

    async def analyze_image(
        self,
        image_bytes: bytes,
        description: str,
    ) -> ImageAnalysisResult:
        """
        Analyze image using a vision-capable Ollama model.
        Returns VISION_UNAVAILABLE if no vision model is available.
        Returns VISION_FAILED if analysis errors out.
        Never fakes results.
        """
        # Detect or re-use the vision model
        if not self._vision_checked or self._detected_model is None:
            self._detected_model = await self._detect_vision_model()
            self._vision_checked = True

        if self._detected_model is None:
            logger.info(
                "Vision analysis skipped — no vision-capable model available in Ollama."
            )
            return ImageAnalysisResult.unavailable(
                "No vision-capable model is available in Ollama. "
                "Install a vision model (e.g. llava) to enable image analysis."
            )

        # Encode image as base64 for Ollama
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        payload = {
            "model": self._detected_model,
            "messages": [
                {"role": "system", "content": _VISION_SYSTEM},
                {
                    "role": "user",
                    "content": f"Incident description: {description}",
                    "images": [b64_image],
                },
            ],
            "stream": False,
            "options": {"temperature": AI_TEMPERATURE},
        }

        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                self._client.post("/api/chat", json=payload),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - t0
            logger.warning("Vision inference timed out after %.2fs", elapsed)
            return ImageAnalysisResult.failed(
                f"Vision inference timed out after {elapsed:.1f}s."
            )
        except Exception as exc:
            logger.warning("Vision inference failed: %s", exc)
            return ImageAnalysisResult.failed(f"Vision provider error: {exc}")

        if resp.status_code != 200:
            logger.warning("Vision Ollama returned HTTP %d", resp.status_code)
            return ImageAnalysisResult.failed(
                f"Ollama returned HTTP {resp.status_code}."
            )

        content = resp.json().get("message", {}).get("content", "").strip()
        logger.debug("Vision raw output: %s", content)

        return self._parse_vision_response(content)

    def _parse_vision_response(self, raw: str) -> ImageAnalysisResult:
        """Parse the model's JSON response into an ImageAnalysisResult."""
        try:
            clean = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if not match:
                raise ValueError("No JSON found in vision response.")
            data: dict[str, Any] = json.loads(match.group())
        except Exception as exc:
            logger.warning("Failed to parse vision response: %s | raw=%r", exc, raw)
            return ImageAnalysisResult.failed(f"Cannot parse vision response: {exc}")

        def _bool(key: str) -> bool:
            val = data.get(key, False)
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes")
            return bool(val)

        try:
            confidence = float(data.get("visual_confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        signals = VisualSignals(
            flood_water=_bool("flood_water"),
            people_visible=_bool("people_visible"),
            structural_damage=_bool("structural_damage"),
            fire_present=_bool("fire_present"),
            smoke_visible=_bool("smoke_visible"),
            road_blocked=_bool("road_blocked"),
            vehicle_submerged=_bool("vehicle_submerged"),
            unsafe_environment=_bool("unsafe_environment"),
            visual_confidence=confidence,
        )

        reason_codes = _build_reason_codes(signals)

        return ImageAnalysisResult(
            vision_available=True,
            visual_signals=signals,
            visual_reason_codes=reason_codes,
            provider_name=self.provider_name,
            model_name=self._detected_model,
        )


def _build_reason_codes(signals: VisualSignals) -> list[VisualReasonCode]:
    """Build reason codes from detected visual signals."""
    codes: list[VisualReasonCode] = []
    if signals.flood_water:
        codes.append(VisualReasonCode.VISUAL_FLOOD_WATER)
    if signals.people_visible:
        codes.append(VisualReasonCode.PEOPLE_VISIBLE)
    if signals.structural_damage:
        codes.append(VisualReasonCode.STRUCTURAL_DAMAGE)
    if signals.fire_present:
        codes.append(VisualReasonCode.FIRE_VISIBLE)
    if signals.smoke_visible:
        codes.append(VisualReasonCode.SMOKE_VISIBLE)
    if signals.road_blocked:
        codes.append(VisualReasonCode.ROAD_BLOCKED)
    if signals.vehicle_submerged:
        codes.append(VisualReasonCode.VEHICLE_SUBMERGED)
    if signals.unsafe_environment:
        codes.append(VisualReasonCode.UNSAFE_ENVIRONMENT)
    if signals.visual_confidence < 0.4:
        codes.append(VisualReasonCode.LOW_VISUAL_CONFIDENCE)
    return codes
