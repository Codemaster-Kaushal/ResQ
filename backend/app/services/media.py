"""Image storage, perceptual hashing, and EXIF extraction.

FR-5 requires EXIF to survive ingestion, so the uploaded bytes are written **verbatim**
— no re-encoding, no stripping. Phase 5's EXIF_CONSISTENT check re-reads the stored file
rather than trusting anything the client said about it.

This work runs inline rather than in the background. It is deterministic, local, and
takes single-digit milliseconds, and Phase 5's duplicate lookup needs the hash to exist
the moment the row does. It is AI scoring — not image handling — that ingestion must
never block on (NFR-1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

import imagehash
from PIL import ExifTags, Image, UnidentifiedImageError

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.time import utcnow

logger = get_logger(__name__)

REPORT_IMAGE_DIR = "reports"

# PIL format name -> file extension.
_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}

_EXIF_DATETIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class ExifSnapshot:
    """What ingestion could read out of the image's own metadata."""

    captured_at: datetime | None = None
    lat: float | None = None
    lng: float | None = None
    camera: str | None = None

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def is_empty(self) -> bool:
        return not any((self.captured_at, self.lat, self.lng, self.camera))


@dataclass(frozen=True)
class StoredImage:
    relative_path: str
    phash: str
    byte_size: int
    exif: ExifSnapshot


class UnsupportedImageType(AppError):
    status_code = 415
    code = ErrorCode.UNSUPPORTED_IMAGE_TYPE


class ImageTooLarge(AppError):
    status_code = 413
    code = ErrorCode.IMAGE_TOO_LARGE


class InvalidImage(AppError):
    status_code = 422
    code = ErrorCode.INVALID_IMAGE


def _rational_to_float(value: object) -> float:
    """EXIF stores GPS components as rationals; Pillow hands them back as IFDRational."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _dms_to_degrees(dms: object, ref: object) -> float | None:
    """Convert (degrees, minutes, seconds) + N/S/E/W reference to signed decimal."""
    try:
        degrees, minutes, seconds = (_rational_to_float(part) for part in dms)  # type: ignore[misc]
    except (TypeError, ValueError):
        return None

    decimal = degrees + minutes / 60 + seconds / 3600
    if isinstance(ref, (str, bytes)):
        marker = ref.decode() if isinstance(ref, bytes) else ref
        if marker.strip().upper() in {"S", "W"}:
            decimal = -decimal
    return round(decimal, 6)


def _parse_exif_datetime(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    for fmt in _EXIF_DATETIME_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def read_exif(image: Image.Image) -> ExifSnapshot:
    """Best-effort EXIF read. Malformed metadata yields an empty snapshot, never an error.

    A camera that writes junk EXIF is not a reason to reject an emergency report.
    """
    try:
        exif = image.getexif()
    except Exception:  # noqa: BLE001 — defensive: Pillow can raise on damaged files
        return ExifSnapshot()

    if not exif:
        return ExifSnapshot()

    camera = " ".join(
        str(exif.get(tag)).strip()
        for tag in (ExifTags.Base.Make.value, ExifTags.Base.Model.value)
        if exif.get(tag)
    ).strip() or None

    captured_at: datetime | None = None
    lat = lng = None

    try:
        detail = exif.get_ifd(ExifTags.IFD.Exif)
        captured_at = _parse_exif_datetime(detail.get(ExifTags.Base.DateTimeOriginal.value))
    except Exception:  # noqa: BLE001
        captured_at = None

    if captured_at is None:
        captured_at = _parse_exif_datetime(exif.get(ExifTags.Base.DateTime.value))

    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if gps:
            lat = _dms_to_degrees(
                gps.get(ExifTags.GPS.GPSLatitude.value),
                gps.get(ExifTags.GPS.GPSLatitudeRef.value),
            )
            lng = _dms_to_degrees(
                gps.get(ExifTags.GPS.GPSLongitude.value),
                gps.get(ExifTags.GPS.GPSLongitudeRef.value),
            )
    except Exception:  # noqa: BLE001
        lat = lng = None

    return ExifSnapshot(captured_at=captured_at, lat=lat, lng=lng, camera=camera)


def read_exif_from_path(path: Path) -> ExifSnapshot:
    """Re-read EXIF from a stored file. Phase 5's EXIF_CONSISTENT check uses this."""
    try:
        with Image.open(path) as handle:
            return read_exif(handle)
    except (FileNotFoundError, UnidentifiedImageError, OSError):
        return ExifSnapshot()


def compute_phash(path: Path) -> str:
    with Image.open(path) as handle:
        return str(imagehash.phash(handle))


def _inspect(data: bytes) -> str:
    """Confirm the bytes really are a supported image, returning the PIL format name.

    The declared content type comes from the client and is not evidence; the file's own
    magic bytes are.
    """
    try:
        with Image.open(BytesIO(data)) as probe:
            probe.verify()  # detects truncation and corruption
        with Image.open(BytesIO(data)) as probe:
            image_format = (probe.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage(
            "The uploaded file could not be read as an image",
            detail={"reason": type(exc).__name__},
        ) from exc

    if image_format not in _EXTENSIONS:
        raise UnsupportedImageType(
            f"Image format {image_format or 'unknown'} is not supported",
            detail={"supported": sorted(_EXTENSIONS)},
        )
    return image_format


def _target_path(extension: str, now: datetime) -> tuple[Path, str]:
    """Date-partitioned so one directory never accumulates every image ever filed.

    The filename is generated, never taken from the upload: a client-supplied name is
    untrusted input and a path-traversal vector.
    """
    relative = f"{REPORT_IMAGE_DIR}/{now:%Y/%m}/{uuid.uuid4().hex}.{extension}"
    absolute = settings.media_dir / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    return absolute, relative


def store_image_bytes(
    data: bytes,
    declared_type: str | None = None,
    fallback_gps: tuple[float, float] | None = None,
) -> StoredImage:
    """Validate, persist, hash, and read the metadata of one uploaded image.

    ``fallback_gps`` is used only when the file carries no EXIF of its own — a
    client that downscaled the photo can hand back the coordinates it read from
    the original, which would otherwise be destroyed by re-encoding. Embedded
    EXIF always wins: it is evidence, the parameter is a claim.
    """
    if not data:
        raise InvalidImage("The uploaded image is empty")

    if len(data) > settings.max_image_bytes:
        raise ImageTooLarge(
            "The uploaded image exceeds the size limit",
            detail={"limit_bytes": settings.max_image_bytes, "received_bytes": len(data)},
        )

    normalised_type = (declared_type or "").split(";")[0].strip().lower()
    if normalised_type and normalised_type not in settings.allowed_image_type_set:
        raise UnsupportedImageType(
            f"Content type {normalised_type} is not accepted",
            detail={"allowed": sorted(settings.allowed_image_type_set)},
        )

    image_format = _inspect(data)
    absolute, relative = _target_path(_EXTENSIONS[image_format], utcnow())

    # Verbatim write — re-encoding here would destroy the EXIF that FR-5 preserves and
    # would shift the perceptual hash away from what the reporter actually sent.
    absolute.write_bytes(data)

    exif = read_exif_from_path(absolute)
    if not exif.has_gps and fallback_gps is not None:
        exif = ExifSnapshot(
            captured_at=exif.captured_at,
            lat=fallback_gps[0],
            lng=fallback_gps[1],
            camera=exif.camera,
        )

    stored = StoredImage(
        relative_path=relative,
        phash=compute_phash(absolute),
        byte_size=len(data),
        exif=exif,
    )

    logger.info(
        "stored report image",
        extra={
            "path": relative,
            "bytes": stored.byte_size,
            "format": image_format,
            "phash": stored.phash,
            "exif_gps": stored.exif.has_gps,
        },
    )
    return stored
