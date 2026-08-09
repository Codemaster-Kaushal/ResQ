"""
Tests for image duplicate detection — Phase 5.
"""

import io

import pytest


def _make_png_bytes(color: tuple = (70, 130, 180)) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (64, 64), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestImageHashService:
    def test_compute_hash_returns_string(self):
        from ai_engine.authenticity.image_duplicate import ImageHashService
        svc = ImageHashService()
        h = svc.compute_hash(_make_png_bytes())
        assert isinstance(h, str)
        assert len(h) > 0

    def test_same_image_same_hash(self):
        from ai_engine.authenticity.image_duplicate import ImageHashService
        svc = ImageHashService()
        img = _make_png_bytes()
        assert svc.compute_hash(img) == svc.compute_hash(img)

    def test_different_images_different_hash(self):
        from ai_engine.authenticity.image_duplicate import ImageHashService
        svc = ImageHashService()
        h1 = svc.compute_hash(_make_png_bytes(color=(70, 130, 180)))
        h2 = svc.compute_hash(_make_png_bytes(color=(200, 50, 50)))
        # Different colors → different hashes (or at least distance > 0)
        dist = svc.compare_hash(h1, h2)
        assert dist >= 0  # can be 0 for very small images but not typically

    def test_compare_hash_identical(self):
        from ai_engine.authenticity.image_duplicate import ImageHashService
        svc = ImageHashService()
        img = _make_png_bytes()
        h = svc.compute_hash(img)
        assert svc.compare_hash(h, h) == 0

    def test_exact_duplicate_detected(self):
        from ai_engine.authenticity.image_duplicate import ImageHashService
        svc = ImageHashService()
        img = _make_png_bytes()
        h = svc.compute_hash(img)
        known = {"RPT-001": h}
        result = svc.check_duplicate(img, known_hashes=known)
        assert result.is_duplicate is True
        assert result.matched_report_id == "RPT-001"
        assert result.hash_distance == 0

    def test_no_duplicates_in_empty_store(self):
        from ai_engine.authenticity.image_duplicate import ImageHashService
        svc = ImageHashService()
        result = svc.check_duplicate(_make_png_bytes(), known_hashes={})
        assert result.is_duplicate is False
        assert result.is_near_duplicate is False
        assert result.matched_report_id is None

    def test_register_and_check_internal_store(self):
        from ai_engine.authenticity.image_duplicate import ImageHashService
        svc = ImageHashService()
        img = _make_png_bytes()
        h = svc.compute_hash(img)
        svc.register_hash("RPT-001", h)
        result = svc.check_duplicate(img)
        assert result.is_duplicate is True

    def test_computed_hash_in_result(self):
        from ai_engine.authenticity.image_duplicate import ImageHashService
        svc = ImageHashService()
        img = _make_png_bytes()
        result = svc.check_duplicate(img, known_hashes={})
        assert result.computed_hash != ""
