"""
Abstract EmbeddingProvider interface — reserved for Phase 7+.
No implementation required in Phase 4-6; interface is defined here
so dependent code can reference it without circular imports.
"""

from abc import ABC, abstractmethod
from typing import List


class EmbeddingProvider(ABC):
    """
    Base interface for text/image embedding providers.

    Hierarchy:
        EmbeddingProvider
            └── <future implementation>
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model tag."""

    # ── Health ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def is_available(self) -> bool:
        """Return True if the provider is reachable."""

    # ── Inference ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        """
        Generate a text embedding vector.

        Args:
            text: Input text.

        Returns:
            List of floats representing the embedding.
        """

    @abstractmethod
    async def embed_image(self, image_bytes: bytes) -> List[float]:
        """
        Generate an image embedding vector.

        Args:
            image_bytes: Raw image bytes.

        Returns:
            List of floats representing the embedding.
        """
