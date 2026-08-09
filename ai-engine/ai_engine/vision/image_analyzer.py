"""
ImageAnalyzer — orchestrates preprocessing and vision analysis.
Falls back gracefully if preprocessing or vision fails.
"""

import logging
from typing import Optional

from ai_engine.config import MAX_IMAGE_SIZE_MB
from ai_engine.providers.vision_base import VisionProvider
from ai_engine.vision.preprocessing import preprocess
from ai_engine.vision.schemas import ExifData, ImageAnalysisResult, VisualReasonCode

logger = logging.getLogger(__name__)


class ImageAnalyzer:
    """
    Combines image preprocessing with vision provider analysis.
    Returns ImageAnalysisResult — never raises.
    """

    def __init__(
        self,
        vision_provider: Optional[VisionProvider] = None,
        max_size_mb: float = MAX_IMAGE_SIZE_MB,
    ) -> None:
        self._provider = vision_provider
        self._max_size_mb = max_size_mb

    async def analyze(
        self,
        image_data: bytes | str,
        description: str = "",
    ) -> ImageAnalysisResult:
        """
        Preprocess and analyze an image.

        Args:
            image_data: Raw bytes or base64 string.
            description: Incident description for context.

        Returns:
            ImageAnalysisResult — never raises; returns VISION_FAILED on error.
        """
        # ── Step 1: Preprocess ────────────────────────────────────────────────
        try:
            preprocessed = preprocess(image_data, max_size_mb=self._max_size_mb)
        except ValueError as exc:
            logger.warning("Image preprocessing failed: %s", exc)
            return ImageAnalysisResult.failed(str(exc))
        except Exception as exc:
            logger.error("Unexpected error during image preprocessing: %s", exc, exc_info=True)
            return ImageAnalysisResult.failed(f"Preprocessing error: {exc}")

        exif = preprocessed.exif_data

        # ── Step 2: Vision analysis (if provider available) ───────────────────
        if self._provider is None:
            result = ImageAnalysisResult.unavailable(
                "No vision provider configured."
            )
        else:
            try:
                result = await self._provider.analyze_image(
                    preprocessed.normalized_bytes,
                    description,
                )
            except Exception as exc:
                logger.error("Vision provider raised unexpectedly: %s", exc, exc_info=True)
                result = ImageAnalysisResult.failed(f"Vision error: {exc}")

        # ── Step 3: Attach EXIF regardless of vision outcome ──────────────────
        result = result.model_copy(update={"exif_data": exif})
        return result
