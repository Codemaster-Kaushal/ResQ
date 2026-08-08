"""Groq provider — very fast, text only.

Text only by design: the image is not sent, so ``visual_severity_modifier`` is forced
to 0 rather than left to a model that cannot see the photograph.
"""

from __future__ import annotations

import httpx

from app.ai.base import ProviderError, TriageResult
from app.ai.parsing import extract_json_object
from app.ai.prompt import SYSTEM_PROMPT, user_prompt
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider:
    name = "groq"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def is_available(self) -> bool:
        return bool(settings.groq_api_key)

    def _payload(self, text: str) -> dict:
        return {
            "model": settings.groq_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                # has_image is False regardless: this provider never sees the image.
                {"role": "user", "content": user_prompt(text, has_image=False)},
            ],
        }

    @staticmethod
    def _text_from(body: dict) -> str:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("groq response had no message content") from exc

        if not isinstance(content, str) or not content.strip():
            raise ProviderError("groq returned empty content")
        return content

    async def classify(self, text: str, image_bytes: bytes | None) -> TriageResult:
        if not settings.groq_api_key:
            raise ProviderError("groq api key is not configured")

        request = {
            "url": API_URL,
            "headers": {"Authorization": f"Bearer {settings.groq_api_key}"},
            "json": self._payload(text),
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
            raise ProviderError(f"groq returned HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"groq request failed: {type(exc).__name__}") from exc

        result = TriageResult.model_validate(extract_json_object(self._text_from(body)))
        # A text-only model has no basis for a visual judgement.
        return result.model_copy(update={"visual_severity_modifier": 0})
