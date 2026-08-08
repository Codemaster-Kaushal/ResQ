"""Provider selection and the fallback chain.

**The router never raises.** That is the single most important guarantee in this
codebase (TRD §10): a provider outage, a rate limit, a timeout, a malformed response,
or a bug inside a provider must all degrade to the local scorer rather than cost a
report its score. Every failure path here ends in a valid TriageResult.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.ai.base import LOCAL_PROVIDER_NAME, ScoringProvider, TriageResult
from app.ai.gemini import GeminiProvider
from app.ai.groq import GroqProvider
from app.ai.local import LocalScorer
from app.config import settings
from app.core.logging import get_logger
from app.models.enums import IncidentType

logger = get_logger(__name__)


@dataclass(frozen=True)
class RoutedTriage:
    """A triage result plus the provenance the governance endpoint reports."""

    result: TriageResult
    provider: str
    attempted: tuple[str, ...]
    degraded: bool


def _safe_floor() -> TriageResult:
    """Last resort if even the local scorer fails — which would mean a bug, not an
    outage. Returning the most neutral valid result keeps ingestion whole; the report
    still exists, still has reasons, and can be re-scored later."""
    return TriageResult(
        incident_type=IncidentType.OTHER,
        life_risk_terms=[],
        people_affected_estimate=None,
        vulnerability_terms=[],
        visual_severity_modifier=0,
        confidence=0.0,
    )


class TriageRouter:
    def __init__(
        self,
        providers: dict[str, ScoringProvider] | None = None,
        order: list[str] | None = None,
    ) -> None:
        self._providers: dict[str, ScoringProvider] = providers or {
            "gemini": GeminiProvider(),
            "groq": GroqProvider(),
            LOCAL_PROVIDER_NAME: LocalScorer(),
        }
        self._order = order

    @property
    def order(self) -> list[str]:
        """Configured chain. `local` is always last, and always present."""
        chain = list(self._order) if self._order is not None else settings.provider_order
        chain = [name for name in chain if name in self._providers]
        if LOCAL_PROVIDER_NAME in self._providers and LOCAL_PROVIDER_NAME not in chain:
            chain.append(LOCAL_PROVIDER_NAME)
        return chain

    def available_providers(self) -> dict[str, bool]:
        result = {}
        for name in self.order:
            try:
                result[name] = bool(self._providers[name].is_available())
            except Exception:  # noqa: BLE001 — availability checks must not throw either
                result[name] = False
        return result

    async def _attempt(
        self, provider: ScoringProvider, text: str, image_bytes: bytes | None
    ) -> TriageResult:
        """One provider, with a timeout and the configured number of retries."""
        attempts = 1 + max(0, settings.ai_retry_attempts)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(
                    provider.classify(text, image_bytes),
                    timeout=settings.ai_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "provider timed out",
                    extra={
                        "provider": provider.name,
                        "attempt": attempt,
                        "timeout_seconds": settings.ai_timeout_seconds,
                    },
                )
            except Exception as exc:  # noqa: BLE001 — any provider fault is recoverable
                last_error = exc
                logger.warning(
                    "provider failed",
                    extra={
                        "provider": provider.name,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                    },
                )

        raise last_error if last_error else RuntimeError("provider produced no result")

    async def classify(self, text: str, image_bytes: bytes | None = None) -> RoutedTriage:
        """Walk the chain and return the first usable result. Never raises."""
        attempted: list[str] = []

        for name in self.order:
            provider = self._providers[name]

            try:
                if not provider.is_available():
                    logger.debug("provider unavailable, skipping", extra={"provider": name})
                    continue
            except Exception:  # noqa: BLE001
                continue

            attempted.append(name)
            try:
                result = await self._attempt(provider, text, image_bytes)
            except Exception:  # noqa: BLE001 — already logged in _attempt
                continue

            return RoutedTriage(
                result=result,
                provider=name,
                attempted=tuple(attempted),
                degraded=name == LOCAL_PROVIDER_NAME and len(attempted) > 1,
            )

        # Every configured provider failed, including the local one. This is a bug
        # rather than an outage, so it is logged loudly — but ingestion still survives.
        logger.error(
            "every scoring provider failed; falling back to the neutral result",
            extra={"attempted": attempted},
        )
        return RoutedTriage(
            result=_safe_floor(),
            provider=LOCAL_PROVIDER_NAME,
            attempted=tuple(attempted),
            degraded=True,
        )


# Module-level default. Tests build their own with injected fakes.
router = TriageRouter()
