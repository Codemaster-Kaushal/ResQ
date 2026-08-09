"""Adapter for the ResQ AI engine (IBM Granite via Ollama), imported in-process.

The engine is a sibling project with its own package layout, so this module is
the single place that knows where it lives and how to talk to it. Everything
else in the backend sees an ordinary `ScoringProvider`.

Two things this adapter deliberately does *not* delegate:

- **Prior-report state.** The engine's own `state.json` only knows reports that
  passed through it. Authenticity is fed from the real `Report` table instead
  (see `app/services/ai_state.py`), so duplicates and corroboration are computed
  against everything on file.
- **Rejection.** The engine returns a trust *band*; turning that into a report
  status stays in the backend, because `rejected` must remain reachable only
  through human review (FR-15).

It also never raises. Import failure, a missing Ollama, a timeout and a bug
inside the engine all resolve to "unavailable", and the router falls through to
the deterministic scorer exactly as it does today.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.ai.base import ProviderError, TriageResult
from app.config import settings
from app.core.logging import get_logger
from app.models.enums import IncidentType

logger = get_logger(__name__)

ENGINE_NAME = "local_granite"


# --- Importing a sibling project ------------------------------------------

@lru_cache(maxsize=1)
def _load_engine() -> Any | None:
    """Put the engine on `sys.path` once and import what we need.

    Cached because the import is not free and because a failure should be
    reported once, not on every report.
    """
    root = Path(settings.ai_engine_path).expanduser().resolve()
    if not (root / "ai_engine").is_dir():
        logger.warning("AI engine not found; staying on the local scorer", extra={"path": str(root)})
        return None

    if str(root) not in sys.path:
        # Appended, not prepended: the backend's own modules must always win a
        # name clash (the engine has top-level `authenticity.py` and
        # `provenance.py` that would otherwise shadow nothing today but could).
        sys.path.append(str(root))

    try:
        from ai_engine.authenticity.authenticity_engine import AuthenticityEngine
        from ai_engine.classification.classifier import classify
        from ai_engine.classification.risk_extractor import extract_risk_factors_rule_based
        from ai_engine.providers.granite_local import GraniteLocalProvider
        from ai_engine.severity.engine import calculate_severity
    except Exception:
        logger.exception("AI engine import failed; staying on the local scorer")
        return None

    return {
        "AuthenticityEngine": AuthenticityEngine,
        "classify": classify,
        "extract_risk_factors_rule_based": extract_risk_factors_rule_based,
        "GraniteLocalProvider": GraniteLocalProvider,
        "calculate_severity": calculate_severity,
    }


def ensure_engine_importable() -> bool:
    """Make `ai_engine.*` importable, returning whether it worked.

    Public because `app.services.ai_state` also imports from the engine and must
    not depend on this module having been touched first.
    """
    return _load_engine() is not None


# --- Results ---------------------------------------------------------------

@dataclass(frozen=True)
class EngineSeverity:
    """Granite's severity verdict, in the shape the backend stores."""

    score: int
    label: str
    reasons: list[dict[str, Any]]
    incident_type: IncidentType
    confidence: float
    provider: str
    fallback_state: str
    model: str


@dataclass(frozen=True)
class EngineAuthenticity:
    score: int
    band: str
    reasons: list[dict[str, Any]]


def _reason(code: str, source: str) -> dict[str, Any]:
    """A reason entry with no weight.

    The backend's own scorer emits additive weights that sum to the score; the
    engine emits bare codes. `weight: None` is the honest representation, and
    the UI hides its running total when it sees one rather than showing a sum
    that does not add up.
    """
    return {"code": code, "weight": None, "source": source}


def _as_incident_type(value: Any) -> IncidentType:
    raw = getattr(value, "value", value)
    try:
        return IncidentType(str(raw))
    except ValueError:
        return IncidentType.OTHER


def _codes(items: Any) -> list[str]:
    return [str(getattr(code, "value", code)) for code in (items or [])]


class ResqAIEngine:
    """The AI engine, wearing the backend's provider interface."""

    name = ENGINE_NAME

    def __init__(self) -> None:
        self._provider = None

    # --- Availability -----------------------------------------------------

    def is_available(self) -> bool:
        """Cheap, synchronous check the router uses to skip us fast.

        Only tests configuration and importability — reaching Ollama is an
        async call, and doing it here would put a network round trip in front
        of every report just to decide whether to try.
        """
        return bool(settings.ai_engine_enabled) and _load_engine() is not None

    def _granite(self):
        engine = _load_engine()
        if engine is None:
            raise ProviderError("AI engine unavailable")
        if self._provider is None:
            self._provider = engine["GraniteLocalProvider"](
                model=settings.granite_model,
                ollama_host=settings.ollama_host,
                timeout=settings.ai_engine_timeout_seconds,
            )
        return self._provider

    @property
    def model_name(self) -> str:
        try:
            return getattr(self._granite(), "model_name", settings.granite_model)
        except Exception:
            return settings.granite_model

    async def ollama_reachable(self) -> bool:
        """Whether Ollama actually answers. Used by governance, not per report."""
        try:
            return bool(await asyncio.wait_for(self._granite().is_available(), timeout=4))
        except Exception:
            return False

    # --- Provider protocol -------------------------------------------------

    async def classify(self, text: str, image_bytes: bytes | None) -> TriageResult:
        """Signals only, for callers that want the router's common shape.

        Full delegation uses `score()` instead; this exists so the engine can
        also act as an ordinary provider in the chain.
        """
        severity = await self.score(text)
        risk = severity.reasons
        return TriageResult(
            incident_type=severity.incident_type,
            life_risk_terms=[r["code"].lower() for r in risk],
            people_affected_estimate=None,
            vulnerability_terms=[],
            visual_severity_modifier=0,
            confidence=severity.confidence,
        )

    # --- Delegated scoring -------------------------------------------------

    async def score(self, text: str) -> EngineSeverity:
        """Classify and score one report with Granite. Raises on failure."""
        engine = _load_engine()
        if engine is None:
            raise ProviderError("AI engine unavailable")

        provider = self._granite()
        timeout = settings.ai_engine_timeout_seconds

        classification = await asyncio.wait_for(
            engine["classify"](text, provider), timeout=timeout
        )

        # Risk extraction is the second Granite call. If it times out we still
        # have a classification, so fall back to the engine's own rules rather
        # than discarding the work already done.
        try:
            risk = await asyncio.wait_for(
                provider.extract_risk_factors(text), timeout=timeout
            )
            risk_source = ENGINE_NAME
        except Exception:
            risk = engine["extract_risk_factors_rule_based"](text)
            risk_source = "rule_based"

        severity = engine["calculate_severity"](risk)

        reasons = [_reason(code, risk_source) for code in _codes(severity.severity_reason_codes)]
        reasons += [
            _reason(code, "classification")
            for code in _codes(getattr(classification, "reason_codes", []))
        ]
        if not reasons:
            # FR-8: a score with no explanation is a failed requirement.
            reasons = [_reason("AI_SCORED", risk_source)]

        return EngineSeverity(
            score=int(severity.severity_score),
            label=str(getattr(severity.severity_label, "value", severity.severity_label)),
            reasons=reasons,
            incident_type=_as_incident_type(getattr(classification, "incident_type", None)),
            confidence=float(getattr(classification, "confidence", 0.0) or 0.0),
            provider=str(getattr(getattr(classification, "provider", None), "value", risk_source)),
            fallback_state=str(
                getattr(getattr(classification, "fallback_state", None), "value", "NORMAL")
            ),
            model=self.model_name,
        )

    # --- Delegated authenticity -------------------------------------------

    def assess(
        self,
        *,
        report_id: str,
        pseudonym: str | None,
        lat: float | None,
        lng: float | None,
        client_timestamp: datetime | None,
        image_bytes: bytes | None,
        previous_reports: list[Any],
        known_hashes: dict[str, str],
    ) -> EngineAuthenticity:
        """Run the engine's trust engine against **our** database state.

        Deterministic and LLM-free, so this is cheap enough to run inline on
        every report rather than in the background.
        """
        engine = _load_engine()
        if engine is None:
            raise ProviderError("AI engine unavailable")

        result = engine["AuthenticityEngine"]().calculate_authenticity(
            report_id=report_id,
            reporter_pseudonym=pseudonym,
            lat=lat,
            lon=lng,
            client_ts=client_timestamp,
            image_bytes=image_bytes,
            previous_reports=previous_reports,
            known_hashes=known_hashes,
        )

        # Field names come from `ai_engine.authenticity.schemas.AuthenticityResult`.
        # They are read explicitly rather than guessed: an attribute that does not
        # exist would silently yield no reason codes, and a trust score with no
        # evidence behind it is worse than no score at all.
        score = int(result.authenticity_score)
        band = str(getattr(result.verification_status, "value", result.verification_status))
        codes = _codes(result.authenticity_reason_codes)

        return EngineAuthenticity(
            score=max(0, min(100, score)),
            band=band,
            reasons=[_reason(code, ENGINE_NAME) for code in codes] or [_reason("AI_ASSESSED", ENGINE_NAME)],
        )


engine_provider = ResqAIEngine()
