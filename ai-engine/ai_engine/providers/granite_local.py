"""
IBM Granite Local Provider — communicates with Ollama (NFR-2, NFR-3).

Runtime flow:
    Application → GraniteLocalProvider → Ollama HTTP API → IBM Granite model

No external network calls are made at runtime after model download.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from ai_engine.config import (
    AI_TEMPERATURE,
    AI_TIMEOUT_SECONDS,
    GRANITE_MODEL,
    GRANITE_MODEL_VERSION,
    OLLAMA_HOST,
)
from ai_engine.exceptions import (
    AIModelUnavailableError,
    AIProviderError,
    AITimeoutError,
)
from ai_engine.providers.base import AIProvider
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.severity import RiskFactors

logger = logging.getLogger(__name__)

# ── Prompt templates ──────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = (
    "You are a disaster-response AI. "
    "Classify the incident into exactly one of these categories: "
    "structural_collapse, flooding, medical, trapped_persons, fire, landslide, infrastructure, other. "
    "Reply ONLY with valid JSON matching this schema exactly: "
    '{{"incident_type": "<category>", "confidence": <0.0-1.0>, "reason_codes": ["<CODE>", ...]}}\n'
    "Valid reason_codes: FLOOD_WATER_DETECTED, WATER_RISING, BUILDING_COLLAPSED, STRUCTURAL_DAMAGE, "
    "PERSONS_TRAPPED, UNABLE_TO_EVACUATE, FIRE_DETECTED, SMOKE_DETECTED, MEDICAL_EMERGENCY, "
    "INJURY_REPORTED, UNCONSCIOUS_PERSON, LANDSLIDE_DETECTED, SLOPE_COLLAPSE, "
    "INFRASTRUCTURE_DAMAGE, ROAD_BLOCKED, POWER_FAILURE, GENERIC_INCIDENT, AI_CLASSIFIED. "
    "Do not include any explanation or extra text."
)

_RISK_SYSTEM = (
    "You are a disaster-response risk analyst. "
    "Extract structured risk factors from the incident description. "
    "Reply ONLY with valid JSON matching this schema exactly: "
    '{{"people_at_risk": <int>, "trapped_persons": <bool>, "medical_emergency": <bool>, '
    '"rapidly_rising_water": <bool>, "structural_damage": <bool>, "fire_present": <bool>, '
    '"infrastructure_failure": <bool>, "evacuation_impossible": <bool>, '
    '"vulnerable_people": <bool>, "environmental_danger": <bool>}}\n'
    "people_at_risk: estimated number of people mentioned (0 if unknown). "
    "Do not include any explanation or extra text."
)


class GraniteLocalProvider(AIProvider):
    """
    Concrete provider for IBM Granite running through a local Ollama server.
    Implements offline-first inference (NFR-2) with no paid API usage (NFR-3).
    """

    def __init__(
        self,
        model: str = GRANITE_MODEL,
        ollama_host: str = OLLAMA_HOST,
        timeout: float = AI_TIMEOUT_SECONDS,
    ) -> None:
        self._model = model
        self._ollama_host = ollama_host.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self._ollama_host,
            timeout=httpx.Timeout(timeout + 2),  # slightly wider than AI timeout
        )

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return "local_granite"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def model_version(self) -> str:
        return GRANITE_MODEL_VERSION

    # ── Health ────────────────────────────────────────────────────────────────

    async def is_available(self) -> bool:
        """Return True if Ollama is running and the model is present."""
        try:
            resp = await self._client.get("/api/tags", timeout=3.0)
            if resp.status_code != 200:
                return False
            data = resp.json()
            model_names = [m.get("name", "") for m in data.get("models", [])]
            # Accept both "model:tag" and "model" variants
            return any(
                self._model == name or self._model == name.split(":")[0]
                for name in model_names
            )
        except Exception as exc:
            logger.debug("Ollama availability check failed: %s", exc)
            return False

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _chat(self, system_prompt: str, user_message: str) -> str:
        """
        Send a chat request to Ollama and return the assistant content string.
        Raises AITimeoutError on timeout, AIProviderError on other failures.
        """
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
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
        except asyncio.TimeoutError as exc:
            elapsed = time.perf_counter() - t0
            logger.warning(
                "Ollama request timed out after %.2fs (limit=%.2fs)",
                elapsed,
                self._timeout,
            )
            raise AITimeoutError(
                f"AI inference exceeded {self._timeout}s timeout.", retryable=True
            ) from exc
        except httpx.ConnectError as exc:
            raise AIModelUnavailableError(
                f"Cannot connect to Ollama at {self._ollama_host}."
            ) from exc
        except Exception as exc:
            raise AIProviderError(f"Ollama request failed: {exc}") from exc

        elapsed = time.perf_counter() - t0
        logger.debug("Ollama response received in %.3fs", elapsed)

        if resp.status_code != 200:
            raise AIProviderError(
                f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        content: str = data.get("message", {}).get("content", "")
        return content.strip()

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """
        Robustly extract the first JSON object from model output.
        Handles markdown code fences and leading/trailing noise.
        """
        # Strip markdown fences
        clean = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        # Find first { ... } block
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object found in model output: {text!r}")
        return json.loads(match.group())

    # ── AIProvider interface ──────────────────────────────────────────────────

    async def analyze_text(self, text: str) -> dict[str, Any]:
        """General-purpose text analysis — returns raw dict."""
        system = (
            "You are a disaster-response AI. Analyse the text and return a JSON summary."
        )
        raw = await self._chat(system, text)
        return self._extract_json(raw)

    async def classify_incident(self, description: str) -> ClassificationResult:
        """
        Classify the incident using IBM Granite.
        Returns a validated ClassificationResult.
        Raises AIProviderError / AITimeoutError on failure.
        """
        logger.info("Classifying incident via Granite: %r", description[:80])
        raw = await self._chat(_CLASSIFY_SYSTEM, description)
        logger.debug("Granite classification raw output: %s", raw)

        data = self._extract_json(raw)

        # Validate incident_type
        try:
            incident_type = IncidentType(data.get("incident_type", "other"))
        except ValueError:
            logger.warning(
                "Granite returned unknown incident_type %r — defaulting to OTHER",
                data.get("incident_type"),
            )
            incident_type = IncidentType.OTHER

        # Validate confidence
        try:
            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        # Validate reason codes
        raw_codes: list = data.get("reason_codes", [])
        valid_codes: list[ClassificationReasonCode] = []
        for code in raw_codes:
            try:
                valid_codes.append(ClassificationReasonCode(code))
            except ValueError:
                logger.debug("Unknown classification reason code ignored: %r", code)

        if not valid_codes:
            valid_codes = [ClassificationReasonCode.AI_CLASSIFIED]

        return ClassificationResult(
            incident_type=incident_type,
            confidence=confidence,
            reason_codes=valid_codes,
            provider=ScoringProvider.LOCAL_GRANITE,
            fallback_state=FallbackState.NORMAL,
        )

    async def extract_risk_factors(self, description: str) -> RiskFactors:
        """
        Extract structured risk factors using IBM Granite.
        Returns a validated RiskFactors model.
        Raises AIProviderError / AITimeoutError on failure.
        """
        logger.info("Extracting risk factors via Granite: %r", description[:80])
        raw = await self._chat(_RISK_SYSTEM, description)
        logger.debug("Granite risk extraction raw output: %s", raw)

        data = self._extract_json(raw)

        # Coerce and clamp each field defensively
        people = int(data.get("people_at_risk", 0)) if data.get("people_at_risk") else 0
        people = max(0, people)

        def _bool(key: str) -> bool:
            val = data.get(key, False)
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes")
            return bool(val)

        return RiskFactors(
            people_at_risk=people,
            trapped_persons=_bool("trapped_persons"),
            medical_emergency=_bool("medical_emergency"),
            rapidly_rising_water=_bool("rapidly_rising_water"),
            structural_damage=_bool("structural_damage"),
            fire_present=_bool("fire_present"),
            infrastructure_failure=_bool("infrastructure_failure"),
            evacuation_impossible=_bool("evacuation_impossible"),
            vulnerable_people=_bool("vulnerable_people"),
            environmental_danger=_bool("environmental_danger"),
        )
