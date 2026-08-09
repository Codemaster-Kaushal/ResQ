"""
Abstract AI provider interface.
All concrete providers (Granite, future cloud) must implement this contract.
"""

from abc import ABC, abstractmethod
from typing import Any

from shared.schemas.classification import ClassificationResult
from shared.schemas.severity import RiskFactors


class AIProvider(ABC):
    """
    Base interface for AI inference providers.

    Hierarchy:
        AIProvider
            └── GraniteLocalProvider   (Phases 1–3)
            └── <future cloud provider>
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier, e.g. 'local_granite'."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model tag as returned by the inference backend."""

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Human-readable version label."""

    # ── Health ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def is_available(self) -> bool:
        """Return True if the provider is reachable and the model is loaded."""

    # ── Inference ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def analyze_text(self, text: str) -> dict[str, Any]:
        """
        General-purpose text analysis.
        Returns a raw dictionary — callers are responsible for validation.
        """

    @abstractmethod
    async def classify_incident(self, description: str) -> ClassificationResult:
        """
        Classify the incident type from free text.
        Must return a valid ClassificationResult.
        On failure, implementations should raise AIProviderError.
        """

    @abstractmethod
    async def extract_risk_factors(self, description: str) -> RiskFactors:
        """
        Extract structured risk factors from incident text.
        Must return a valid RiskFactors model.
        On failure, implementations should raise AIProviderError.
        """
