"""
Image preprocessing utilities for Phase 4.
Validates, decodes, normalizes, and extracts EXIF from images.
Never modifies original evidence bytes.
"""

import base64
import binascii
import io
import logging
from typing import Optional

from ai_engine.vision.schemas import ExifData, PreprocessedImage

logger = logging.getLogger(__name__)

# Supported MIME types and their PIL format names
_SUPPORTED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_MAX_NORMALIZE_PX = 1024  # Max dimension for analysis copy


def decode_image(data: bytes | str) -> bytes:
    """
    Safely decode raw bytes or base64-encoded string to raw image bytes.

    Args:
        data: Raw bytes or base64 string (with or without data URI prefix).

    Returns:
        Raw image bytes.

    Raises:
        ValueError: If data is not valid bytes or base64.
    """
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        # Strip data URI prefix if present: data:image/jpeg;base64,...
        if data.startswith("data:"):
            try:
                _, encoded = data.split(",", 1)
            except ValueError:
                raise ValueError("Malformed data URI string for image.")
            data = encoded
        try:
            return base64.b64decode(data)
        except (binascii.Error, Exception) as exc:
            raise ValueError(f"Invalid base64-encoded image data: {exc}") from exc
    raise ValueError(f"Unsupported image data type: {type(data)}")


def validate_image(data: bytes | str, max_size_mb: float = 10.0) -> bytes:
    """
    Validate image format (JPEG, PNG, WEBP) and size.

    Args:
        data: Raw bytes or base64 string.
        max_size_mb: Maximum accepted size in megabytes.

    Returns:
        Raw image bytes if valid.

    Raises:
        ValueError: If format is unsupported or size exceeds limit.
    """
    try:
        raw = decode_image(data)
    except ValueError:
        raise

    # Size check
    size_mb = len(raw) / (1024 * 1024)
    if size_mb > max_size_mb:
        raise ValueError(
            f"Image size {size_mb:.1f}MB exceeds maximum {max_size_mb}MB."
        )

    # Format check using Pillow
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as img:
            fmt = img.format
        if fmt not in _SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported image format '{fmt}'. Supported: JPEG, PNG, WEBP."
            )
    except ImportError:
        logger.warning("Pillow not available — skipping format validation.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Cannot read image: {exc}") from exc

    return raw


def extract_exif(image_bytes: bytes) -> Optional[ExifData]:
    """
    Extract EXIF metadata safely from image bytes.
    Returns None if EXIF is not available or extraction fails.

    Args:
        image_bytes: Raw image bytes.

    Returns:
        ExifData or None.
    """
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
    except ImportError:
        logger.debug("Pillow not available — skipping EXIF extraction.")
        return None

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
            raw_exif = img._getexif()  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("EXIF extraction failed: %s", exc)
        return None

    if not raw_exif:
        return ExifData(width=width, height=height)

    exif: dict = {}
    for tag_id, value in raw_exif.items():
        tag = TAGS.get(tag_id, tag_id)
        exif[tag] = value

    # Parse timestamp
    timestamp = None
    for ts_key in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
        if ts_key in exif:
            try:
                from datetime import datetime
                timestamp = datetime.strptime(str(exif[ts_key]), "%Y:%m:%d %H:%M:%S")
                break
            except Exception:
                pass

    # Parse GPS
    gps_lat, gps_lon = None, None
    gps_info_raw = exif.get("GPSInfo")
    if gps_info_raw and isinstance(gps_info_raw, dict):
        try:
            gps_info = {GPSTAGS.get(k, k): v for k, v in gps_info_raw.items()}
            lat = _dms_to_decimal(gps_info.get("GPSLatitude"), gps_info.get("GPSLatitudeRef", "N"))
            lon = _dms_to_decimal(gps_info.get("GPSLongitude"), gps_info.get("GPSLongitudeRef", "E"))
            gps_lat = lat
            gps_lon = lon
        except Exception as exc:
            logger.debug("GPS EXIF parsing failed: %s", exc)

    return ExifData(
        timestamp=timestamp,
        gps_latitude=gps_lat,
        gps_longitude=gps_lon,
        width=width,
        height=height,
        make=str(exif.get("Make", "")) or None,
        model=str(exif.get("Model", "")) or None,
        software=str(exif.get("Software", "")) or None,
    )


def normalize_image(image_bytes: bytes, max_dim: int = _MAX_NORMALIZE_PX) -> bytes:
    """
    Create an analysis copy of the image resized to max_dim on the longest side.
    Never modifies the original bytes — returns new bytes.

    Args:
        image_bytes: Raw image bytes.
        max_dim: Maximum dimension (width or height).

    Returns:
        JPEG-encoded bytes of the resized image.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not available — returning original bytes unchanged.")
        return image_bytes

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Convert to RGB for uniform JPEG output (handles RGBA, P, etc.)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_dim:
                if w >= h:
                    new_w, new_h = max_dim, int(h * max_dim / w)
                else:
                    new_w, new_h = int(w * max_dim / h), max_dim
                img = img.resize((new_w, new_h))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
    except Exception as exc:
        logger.warning("Image normalization failed (%s) — returning original.", exc)
        return image_bytes


def preprocess(data: bytes | str, max_size_mb: float = 10.0) -> PreprocessedImage:
    """
    Full preprocessing pipeline: validate → decode → extract EXIF → normalize.

    Args:
        data: Raw bytes or base64 string.
        max_size_mb: Maximum accepted size in megabytes.

    Returns:
        PreprocessedImage dataclass.

    Raises:
        ValueError: On invalid format or size.
    """
    raw = validate_image(data, max_size_mb=max_size_mb)
    exif = extract_exif(raw)
    normalized = normalize_image(raw)

    # Detect MIME type
    mime_type = "image/jpeg"
    try:
        from PIL import Image
        import io as _io
        with Image.open(_io.BytesIO(raw)) as img:
            fmt = img.format or "JPEG"
            width, height = img.size
            mime_type = _SUPPORTED_FORMATS.get(fmt, "image/jpeg")
    except Exception:
        width, height = 0, 0

    return PreprocessedImage(
        original_bytes=raw,
        normalized_bytes=normalized,
        exif_data=exif,
        mime_type=mime_type,
        width=width,
        height=height,
        file_size_bytes=len(raw),
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _dms_to_decimal(dms, ref: str) -> Optional[float]:
    """Convert DMS tuple to decimal degrees."""
    if dms is None:
        return None
    try:
        degrees = float(dms[0])
        minutes = float(dms[1])
        seconds = float(dms[2])
        decimal = degrees + minutes / 60 + seconds / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 7)
    except Exception:
        return None
