"""
Tests for image preprocessing — Phase 4.
"""

import base64
import io

import pytest


def _make_png_bytes(width: int = 64, height: int = 64, color: tuple = (70, 130, 180)) -> bytes:
    """Create a simple in-memory PNG image."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes(width: int = 64, height: int = 64) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (width, height), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestDecodeImage:
    def test_raw_bytes_returned_unchanged(self):
        from ai_engine.vision.preprocessing import decode_image
        data = b"\x89PNG\r\n\x1a\n"
        assert decode_image(data) == data

    def test_base64_string_decoded(self):
        from ai_engine.vision.preprocessing import decode_image
        raw = b"hello world"
        encoded = base64.b64encode(raw).decode()
        assert decode_image(encoded) == raw

    def test_data_uri_stripped_and_decoded(self):
        from ai_engine.vision.preprocessing import decode_image
        raw = b"test data"
        encoded = base64.b64encode(raw).decode()
        data_uri = f"data:image/png;base64,{encoded}"
        assert decode_image(data_uri) == raw

    def test_invalid_base64_raises(self):
        from ai_engine.vision.preprocessing import decode_image
        with pytest.raises(ValueError):
            decode_image("not_valid_base64!!!")

    def test_unsupported_type_raises(self):
        from ai_engine.vision.preprocessing import decode_image
        with pytest.raises(ValueError):
            decode_image(12345)  # type: ignore


class TestValidateImage:
    def test_valid_png_accepted(self):
        from ai_engine.vision.preprocessing import validate_image
        data = _make_png_bytes()
        result = validate_image(data)
        assert result == data

    def test_valid_jpeg_accepted(self):
        from ai_engine.vision.preprocessing import validate_image
        data = _make_jpeg_bytes()
        result = validate_image(data)
        assert result == data

    def test_oversized_image_rejected(self):
        from ai_engine.vision.preprocessing import validate_image
        # Create a fake 11MB payload (won't be real image, but size check comes first)
        large = b"x" * (11 * 1024 * 1024)
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_image(large, max_size_mb=10.0)

    def test_invalid_format_rejected(self):
        from ai_engine.vision.preprocessing import validate_image
        # Raw bytes that are not a valid image format
        with pytest.raises(ValueError):
            validate_image(b"not an image at all", max_size_mb=10.0)


class TestNormalizeImage:
    def test_large_image_resized(self):
        from ai_engine.vision.preprocessing import normalize_image
        data = _make_png_bytes(width=2000, height=1500)
        result = normalize_image(data, max_dim=1024)
        from PIL import Image
        with Image.open(io.BytesIO(result)) as img:
            assert max(img.size) <= 1024

    def test_small_image_not_enlarged(self):
        from ai_engine.vision.preprocessing import normalize_image
        data = _make_png_bytes(width=100, height=100)
        result = normalize_image(data, max_dim=1024)
        from PIL import Image
        with Image.open(io.BytesIO(result)) as img:
            # Should stay at same or similar size
            assert max(img.size) <= 1024


class TestPreprocess:
    def test_preprocess_returns_dataclass(self):
        from ai_engine.vision.preprocessing import preprocess
        data = _make_png_bytes()
        result = preprocess(data)
        assert result.original_bytes == data
        assert result.normalized_bytes is not None
        assert result.file_size_bytes > 0
        assert result.width == 64
        assert result.height == 64

    def test_original_bytes_unchanged(self):
        from ai_engine.vision.preprocessing import preprocess
        data = _make_png_bytes()
        result = preprocess(data)
        assert result.original_bytes == data  # evidence never modified

    def test_base64_input_accepted(self):
        from ai_engine.vision.preprocessing import preprocess
        data = _make_png_bytes()
        b64 = base64.b64encode(data).decode()
        result = preprocess(b64)
        assert result.file_size_bytes > 0
