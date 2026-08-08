"""Deterministic seed imagery.

Images are generated rather than shipped as binaries: the repository stays text-only,
and the same bytes are produced on every machine, which is what makes the perceptual
hashes reproducible (NFR-4).

The pair ``collapse-scene`` / ``collapse-scene-reshared`` is the deliberate duplicate:
the second is the first cropped, rescaled and re-encoded at lower quality — what
actually happens to a photograph forwarded through a messaging app. Its perceptual hash
stays within the duplicate threshold while its bytes differ completely.
"""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

import imagehash
import piexif
from PIL import Image, ImageDraw, ImageFilter

IMAGE_SIZE = (320, 240)
JPEG_QUALITY = 88
RESHARE_QUALITY = 68

# Seed image ids that are re-encodes of another image, mapped to their source.
RESHARES: dict[str, str] = {"collapse-scene-reshared": "collapse-scene"}


def _palette(rng: random.Random) -> tuple[tuple[int, int, int], ...]:
    base_hue = rng.randint(0, 255)
    return tuple(
        (
            (base_hue + offset) % 256,
            rng.randint(40, 210),
            rng.randint(40, 210),
        )
        for offset in (0, 60, 120, 180)
    )


def render(image_id: str) -> Image.Image:
    """Build a distinct image from its id. Same id in, same pixels out."""
    rng = random.Random(f"rescuenet-seed:{image_id}")
    colours = _palette(rng)

    image = Image.new("RGB", IMAGE_SIZE, colours[0])
    draw = ImageDraw.Draw(image)

    width, height = IMAGE_SIZE

    # A horizon line varies overall luminance layout, which is what pHash keys on.
    horizon = rng.randint(height // 4, (height * 3) // 4)
    draw.rectangle([0, horizon, width, height], fill=colours[1])

    for _ in range(rng.randint(5, 9)):
        x0 = rng.randint(-20, width - 40)
        y0 = rng.randint(-20, height - 40)
        x1 = x0 + rng.randint(30, 160)
        y1 = y0 + rng.randint(30, 140)
        colour = colours[rng.randint(0, len(colours) - 1)]
        if rng.random() < 0.5:
            draw.rectangle([x0, y0, x1, y1], fill=colour)
        else:
            draw.ellipse([x0, y0, x1, y1], fill=colour)

    for _ in range(rng.randint(3, 7)):
        draw.line(
            [
                rng.randint(0, width),
                rng.randint(0, height),
                rng.randint(0, width),
                rng.randint(0, height),
            ],
            fill=colours[rng.randint(0, len(colours) - 1)],
            width=rng.randint(2, 9),
        )

    return image.filter(ImageFilter.GaussianBlur(radius=0.6))


def reshare(image: Image.Image) -> Image.Image:
    """Simulate a forwarded photograph: slight crop, rescale, quality loss."""
    width, height = image.size
    return image.crop((3, 3, width - 3, height - 3)).resize(image.size, Image.BICUBIC)


def _to_dms(value: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    value = abs(value)
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60 * 100)
    return ((degrees, 1), (minutes, 1), (seconds, 100))


def exif_bytes(lat: float, lng: float, captured_at: datetime) -> bytes:
    """Build camera-style EXIF, so Phase 5's EXIF_CONSISTENT check has real input."""
    payload = {
        "0th": {
            piexif.ImageIFD.Make: b"RescueNet",
            piexif.ImageIFD.Model: b"FieldCam 1",
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: captured_at.strftime("%Y:%m:%d %H:%M:%S").encode()
        },
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lng >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _to_dms(lng),
        },
    }
    return piexif.dump(payload)


def write(
    image_id: str,
    directory: Path,
    gps: tuple[float, float] | None = None,
    captured_at: datetime | None = None,
) -> Path:
    """Render and save one seed image, returning its path.

    EXIF is only attached when GPS is supplied. Perceptual hashing reads pixels, not
    metadata, so adding it cannot disturb the duplicate-pair distance.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{image_id}.jpg"

    source = RESHARES.get(image_id)
    if source is None:
        image, quality = render(image_id), JPEG_QUALITY
    else:
        image, quality = reshare(render(source)), RESHARE_QUALITY

    options: dict = {"quality": quality}
    if gps is not None:
        options["exif"] = exif_bytes(gps[0], gps[1], captured_at or datetime(2026, 8, 8, 9, 0))

    image.save(path, "JPEG", **options)
    return path


def phash_of(path: Path) -> str:
    """Perceptual hash of a stored file — the same route ingestion takes in Phase 3."""
    with Image.open(path) as handle:
        return str(imagehash.phash(handle))


def hamming(left: str, right: str) -> int:
    return imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right)
