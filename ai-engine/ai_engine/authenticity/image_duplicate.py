"""
ImageHashService — perceptual hashing for duplicate image detection (FR-12).
Uses pHash (imagehash library) for robust near-duplicate detection.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from ai_engine.config import IMAGE_DUPLICATE_HASH_DISTANCE

logger = logging.getLogger(__name__)


@dataclass
class DuplicateCheckResult:
    """Result of a duplicate image check."""
    is_duplicate: bool
    is_near_duplicate: bool
    hash_distance: Optional[int]
    matched_report_id: Optional[str]
    computed_hash: str


class ImageHashService:
    """
    Computes and compares perceptual hashes for image deduplication.
    Uses an in-memory hash store (Phase 5).
    Person 2 can replace this with a DB-backed implementation by subclassing.
    """

    def __init__(self, threshold: int = IMAGE_DUPLICATE_HASH_DISTANCE) -> None:
        self._threshold = threshold
        # In-memory store: report_id -> hash string
        self._hash_store: dict[str, str] = {}

    def compute_hash(self, image_bytes: bytes) -> str:
        """
        Compute a perceptual hash (pHash) of an image.

        Args:
            image_bytes: Raw image bytes.

        Returns:
            Hex string of the pHash.

        Raises:
            ValueError: If hashing fails.
        """
        try:
            import imagehash
            from PIL import Image
            import io
            with Image.open(io.BytesIO(image_bytes)) as img:
                h = imagehash.phash(img)
            return str(h)
        except ImportError as exc:
            raise ValueError("imagehash or Pillow not installed.") from exc
        except Exception as exc:
            raise ValueError(f"Failed to compute image hash: {exc}") from exc

    def compare_hash(self, hash1: str, hash2: str) -> int:
        """
        Compute Hamming distance between two perceptual hash strings.

        Args:
            hash1: First hash string.
            hash2: Second hash string.

        Returns:
            Integer Hamming distance (0 = identical).

        Raises:
            ValueError: If hashes are malformed.
        """
        try:
            import imagehash
            h1 = imagehash.hex_to_hash(hash1)
            h2 = imagehash.hex_to_hash(hash2)
            return int(h1 - h2)
        except Exception as exc:
            raise ValueError(f"Failed to compare hashes: {exc}") from exc

    def register_hash(self, report_id: str, image_hash: str) -> None:
        """
        Register a computed hash in the in-memory store.

        Args:
            report_id: Report identifier.
            image_hash: Computed hash string.
        """
        self._hash_store[report_id] = image_hash
        logger.debug("Registered hash for report %s: %s", report_id, image_hash)

    def check_duplicate(
        self,
        image_bytes: bytes,
        known_hashes: Optional[dict[str, str]] = None,
    ) -> DuplicateCheckResult:
        """
        Check whether the image is a duplicate of any known image.

        Args:
            image_bytes: Raw image bytes.
            known_hashes: Optional external dict of report_id -> hash_string.
                          If None, uses the internal in-memory store.

        Returns:
            DuplicateCheckResult.
        """
        # Compute hash for the incoming image
        try:
            new_hash = self.compute_hash(image_bytes)
        except ValueError as exc:
            logger.warning("Cannot compute image hash: %s", exc)
            return DuplicateCheckResult(
                is_duplicate=False,
                is_near_duplicate=False,
                hash_distance=None,
                matched_report_id=None,
                computed_hash="",
            )

        store = known_hashes if known_hashes is not None else self._hash_store

        best_distance: Optional[int] = None
        best_match: Optional[str] = None

        for report_id, existing_hash in store.items():
            try:
                dist = self.compare_hash(new_hash, existing_hash)
            except ValueError:
                continue
            if best_distance is None or dist < best_distance:
                best_distance = dist
                best_match = report_id

        is_duplicate = best_distance is not None and best_distance == 0
        is_near_duplicate = (
            best_distance is not None
            and 0 < best_distance <= self._threshold
        )

        logger.debug(
            "Duplicate check: hash=%s best_distance=%s match=%s",
            new_hash, best_distance, best_match,
        )

        return DuplicateCheckResult(
            is_duplicate=is_duplicate,
            is_near_duplicate=is_near_duplicate,
            hash_distance=best_distance,
            matched_report_id=best_match if (is_duplicate or is_near_duplicate) else None,
            computed_hash=new_hash,
        )
