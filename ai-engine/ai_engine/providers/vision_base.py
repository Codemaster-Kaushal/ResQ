"""
Abstract VisionProvider interface.
All concrete vision providers must implement this contract.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_engine.vision.schemas import ImageAnalysisResult


class VisionProvider(ABC):
    """
    Base interface for image/vision inference providers.

    Hierarchy:
        VisionProvider
            └── GraniteVisionProvider  (Phase 4)
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier, e.g. 'granite_vision'."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model tag as returned by the inference backend."""

    # ── Health ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def is_available(self) -> bool:
        """Return True if the provider is reachable and vision-capable."""

    # ── Inference ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def analyze_image(
        self,
        image_bytes: bytes,
        description: str,
    ) -> "ImageAnalysisResult":
        """
        Analyze an image in context of the incident description.

        Args:
            image_bytes: Raw image bytes (JPEG, PNG, or WEBP).
            description: Incident free-text description for context.

        Returns:
            ImageAnalysisResult — never raises; returns VISION_UNAVAILABLE/VISION_FAILED on error.
        """
