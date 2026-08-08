"""Google Gemini Flash provider — the only free-tier option with vision.

Confirm the current free-tier quota before relying on this in a demo. The local scorer
exists precisely so that a rate limit is an inconvenience rather than an outage.
"""

from __future__ import annotations

import base64

import httpx

from app.ai.base import ProviderError, TriageResult
from app.ai.parsing import extract_json_object
from app.ai.prompt import SYSTEM_PROMPT, user_prompt
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider:
    name = "gemini"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # Injectable so tests can drive the request builder without a network.
        self._client = client

    def is_available(self) -> bool:
        return bool(settings.gemini_api_key)

    def _payload(self, text: str, image_bytes: bytes | None) -> dict:
        parts: list[dict] = [{"text": user_prompt(text, image_bytes is not None)}]
        if image_bytes:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                }
            )
        return {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.1,  # extraction, not creativity
                "responseMimeType": "application/json",
            },
        }

    @staticmethod
    def _text_from(body: dict) -> str:
        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("gemini response had no candidate content") from exc

        joined = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if not joined.strip():
            raise ProviderError("gemini returned empty content")
        return joined

    async def classify(self, text: str, image_bytes: bytes | None) -> TriageResult:
        if not settings.gemini_api_key:
            raise ProviderError("gemini api key is not configured")

        url = f"{API_ROOT}/{settings.gemini_model}:generateContent"
        request = {
            "url": url,
            "params": {"key": settings.gemini_api_key},
            "json": self._payload(text, image_bytes),
        }

        try:
            if self._client is not None:
                response = await self._client.post(**request)
            else:
                async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
                    response = await client.post(**request)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"gemini returned HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"gemini request failed: {type(exc).__name__}") from exc

        payload = extract_json_object(self._text_from(body))
        return TriageResult.model_validate(payload)
