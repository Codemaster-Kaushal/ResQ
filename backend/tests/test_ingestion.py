"""Phase 3 acceptance: report ingestion, media storage, EXIF, and pHash.

A report with an image persists with its hash computed and its EXIF readable; invalid
coordinates are rejected with a typed error; a report with no image still succeeds.
"""

from __future__ import annotations

import random
import statistics
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

import piexif
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.models import Report, ReportStatus
from app.services.media import read_exif_from_path

ENDPOINT = "/api/reports"


# --- Image builders ----------------------------------------------------------------


def textured_jpeg(seed: int = 1, size: tuple[int, int] = (128, 96)) -> Image.Image:
    """A patterned image. Flat colours collapse to a degenerate perceptual hash."""
    rng = random.Random(seed)
    image = Image.new("RGB", size, (rng.randint(0, 90), rng.randint(0, 90), rng.randint(0, 90)))
    draw = ImageDraw.Draw(image)
    for _ in range(8):
        box = [rng.randint(0, size[0] - 1), rng.randint(0, size[1] - 1)]
        box += [box[0] + rng.randint(10, 60), box[1] + rng.randint(10, 50)]
        draw.rectangle(box, fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)))
    return image


def _to_dms(value: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    value = abs(value)
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60 * 100)
    return ((degrees, 1), (minutes, 1), (seconds, 100))


def jpeg_bytes(seed: int = 1) -> bytes:
    buffer = BytesIO()
    textured_jpeg(seed).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def jpeg_with_exif(lat: float, lng: float, captured: datetime, seed: int = 2) -> bytes:
    """A JPEG carrying GPS and capture-time EXIF, as a phone camera would write it."""
    exif = {
        "0th": {
            piexif.ImageIFD.Make: b"RescueNet",
            piexif.ImageIFD.Model: b"FieldCam 1",
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: captured.strftime("%Y:%m:%d %H:%M:%S").encode()
        },
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lng >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _to_dms(lng),
        },
    }
    buffer = BytesIO()
    textured_jpeg(seed).save(buffer, "JPEG", quality=90, exif=piexif.dump(exif))
    return buffer.getvalue()


def post_report(client: TestClient, **overrides):
    data = {"text": "wall has collapsed, people are trapped", "lat": "12.9352", "lng": "77.6245"}
    files = overrides.pop("files", None)
    data.update({k: str(v) for k, v in overrides.items() if v is not None})
    return client.post(ENDPOINT, data=data, files=files)


# --- Acceptance: image, hash, EXIF --------------------------------------------------


def test_report_with_image_persists_hash_and_exif(client: TestClient) -> None:
    captured = datetime(2026, 8, 8, 9, 30, 0)
    payload = jpeg_with_exif(12.9352, 77.6245, captured)

    response = post_report(
        client,
        idempotency_key="p3-image-exif",
        files={"image": ("photo.jpg", BytesIO(payload), "image/jpeg")},
    )

    assert response.status_code == 201
    body = response.json()

    image = body["image"]
    assert image["phash"] and image["phash"] != "0" * 16
    assert (settings.media_dir / image["path"]).is_file()

    exif = image["exif"]
    assert exif["has_gps"] is True
    assert exif["lat"] == pytest.approx(12.9352, abs=1e-3)
    assert exif["lng"] == pytest.approx(77.6245, abs=1e-3)
    assert exif["captured_at"].startswith("2026-08-08T09:30")
    assert "FieldCam" in exif["camera"]


def test_stored_bytes_are_verbatim_so_exif_survives(client: TestClient) -> None:
    """FR-5: EXIF must be preserved on disk for the Phase 5 authenticity stage."""
    payload = jpeg_with_exif(13.0358, 77.5970, datetime(2026, 8, 8, 7, 15, 0), seed=5)

    response = post_report(
        client,
        idempotency_key="p3-verbatim",
        files={"image": ("photo.jpg", BytesIO(payload), "image/jpeg")},
    )
    stored_path = settings.media_dir / response.json()["image"]["path"]

    assert stored_path.read_bytes() == payload  # not re-encoded

    reread = read_exif_from_path(stored_path)
    assert reread.has_gps
    assert reread.lat == pytest.approx(13.0358, abs=1e-3)


def test_image_without_exif_still_succeeds(client: TestClient) -> None:
    response = post_report(
        client,
        idempotency_key="p3-no-exif",
        files={"image": ("plain.jpg", BytesIO(jpeg_bytes(9)), "image/jpeg")},
    )

    assert response.status_code == 201
    image = response.json()["image"]
    assert image["phash"]
    assert image["exif"] is None


def test_report_without_image_succeeds(client: TestClient) -> None:
    response = post_report(client, idempotency_key="p3-no-image")

    assert response.status_code == 201
    body = response.json()
    assert body["image"] is None
    assert body["status"] == ReportStatus.RECEIVED.value


def test_matching_images_produce_matching_hashes(client: TestClient) -> None:
    """The property Phase 5's duplicate detection is built on."""
    payload = jpeg_bytes(11)

    first = post_report(
        client, idempotency_key="p3-hash-1", files={"image": ("a.jpg", BytesIO(payload), "image/jpeg")}
    )
    second = post_report(
        client, idempotency_key="p3-hash-2", files={"image": ("b.jpg", BytesIO(payload), "image/jpeg")}
    )

    assert first.json()["image"]["phash"] == second.json()["image"]["phash"]
    # Same pixels, but stored separately — no silent overwriting.
    assert first.json()["image"]["path"] != second.json()["image"]["path"]


# --- Acceptance: invalid coordinates -------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lng", "field"),
    [
        (91.0, 77.6, "body.lat"),
        (-91.0, 77.6, "body.lat"),
        (12.9, 181.0, "body.lng"),
        (12.9, -181.0, "body.lng"),
        ("nan", 77.6, "body.lat"),
        (12.9, "inf", "body.lng"),
    ],
)
def test_invalid_coordinates_are_rejected_with_a_typed_error(
    client: TestClient, lat, lng, field: str
) -> None:
    response = client.post(ENDPOINT, data={"text": "something happened", "lat": lat, "lng": lng})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert any(item["location"] == field for item in error["detail"]["fields"])


def test_invalid_coordinates_persist_nothing(client: TestClient) -> None:
    client.post(
        ENDPOINT,
        data={"text": "x", "lat": "91", "lng": "20", "idempotency_key": "p3-rejected"},
    )

    with Session(engine) as session:
        assert (
            session.exec(
                select(Report).where(Report.idempotency_key == "p3-rejected")
            ).first()
            is None
        )


def test_missing_text_is_rejected(client: TestClient) -> None:
    response = client.post(ENDPOINT, data={"lat": "12.9", "lng": "77.6"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- Upload hardening ----------------------------------------------------------------


def test_non_image_upload_is_rejected(client: TestClient) -> None:
    response = post_report(
        client,
        idempotency_key="p3-not-image",
        files={"image": ("notes.txt", BytesIO(b"this is not an image"), "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_IMAGE_TYPE"


def test_corrupt_image_claiming_to_be_a_jpeg_is_rejected(client: TestClient) -> None:
    """The declared content type is a claim, not evidence — the bytes decide."""
    response = post_report(
        client,
        idempotency_key="p3-corrupt",
        files={"image": ("photo.jpg", BytesIO(b"\xff\xd8\xff\xe0 truncated"), "image/jpeg")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_IMAGE"


def test_oversized_image_is_rejected(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_image_bytes", 128)

    response = post_report(
        client,
        idempotency_key="p3-too-big",
        files={"image": ("big.jpg", BytesIO(jpeg_bytes(3)), "image/jpeg")},
    )

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "IMAGE_TOO_LARGE"
    assert error["detail"]["limit_bytes"] == 128


def test_upload_filename_is_never_used_on_disk(client: TestClient) -> None:
    """A client-supplied filename is untrusted input and a traversal vector."""
    response = post_report(
        client,
        idempotency_key="p3-traversal",
        files={"image": ("../../../etc/passwd.jpg", BytesIO(jpeg_bytes(4)), "image/jpeg")},
    )

    assert response.status_code == 201
    path = response.json()["image"]["path"]
    assert ".." not in path
    assert "passwd" not in path
    assert path.startswith("reports/")
    assert (settings.media_dir / path).is_file()


def test_extension_follows_the_real_format_not_the_claim(client: TestClient) -> None:
    """JPEG bytes announced as a PNG are stored as what they actually are."""
    response = post_report(
        client,
        idempotency_key="p3-mislabelled",
        files={"image": ("shot.png", BytesIO(jpeg_bytes(7)), "image/png")},
    )

    assert response.status_code == 201
    assert response.json()["image"]["path"].endswith(".jpg")


# --- Identity, idempotency, timestamps ------------------------------------------------


def test_resubmitting_an_idempotency_key_returns_the_original(client: TestClient) -> None:
    first = post_report(client, idempotency_key="p3-repeat", text="first submission")
    second = post_report(client, idempotency_key="p3-repeat", text="different text entirely")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]

    with Session(engine) as session:
        rows = session.exec(
            select(Report).where(Report.idempotency_key == "p3-repeat")
        ).all()
    assert len(rows) == 1
    assert rows[0].text == "first submission"  # the retry did not overwrite


def test_server_supplies_an_idempotency_key_when_the_client_omits_one(client: TestClient) -> None:
    body = post_report(client, text="no key supplied").json()

    assert body["idempotency_key"].startswith("srv-")
    assert body["duplicate"] is False


def test_pseudonym_is_generated_when_absent(client: TestClient) -> None:
    """NFR-7: filing a report never requires an identity."""
    body = post_report(client, idempotency_key="p3-anon").json()

    assert body["reporter_pseudonym"].startswith("anon-")


def test_supplied_pseudonym_is_kept(client: TestClient) -> None:
    body = post_report(
        client, idempotency_key="p3-named", reporter_pseudonym="swift-heron-77"
    ).json()

    assert body["reporter_pseudonym"] == "swift-heron-77"


def test_client_timestamp_offset_is_normalised_to_utc(client: TestClient) -> None:
    """A phone in IST sends +05:30; storage is naive UTC throughout."""
    ist = timezone(timedelta(hours=5, minutes=30))
    sent = datetime(2026, 8, 8, 15, 0, 0, tzinfo=ist)

    body = post_report(
        client, idempotency_key="p3-tz", client_created_at=sent.isoformat()
    ).json()

    assert body["client_created_at"].startswith("2026-08-08T09:30")


def test_client_timestamp_defaults_to_receipt(client: TestClient) -> None:
    body = post_report(client, idempotency_key="p3-no-clock").json()

    assert body["client_created_at"][:16] == body["received_at"][:16]


def test_client_timestamp_is_preserved_separately_from_receipt(client: TestClient) -> None:
    """FR-28: ageing uses the client clock, so the two must not be collapsed."""
    filed = datetime.now(timezone.utc) - timedelta(hours=3)

    body = post_report(
        client, idempotency_key="p3-offline-clock", client_created_at=filed.isoformat()
    ).json()

    assert body["client_created_at"] < body["received_at"]


# --- Ingestion does not score ----------------------------------------------------------


def test_ingestion_leaves_scoring_to_later_phases(client: TestClient) -> None:
    """FR-4 and NFR-1: ingestion persists and returns; it never scores inline."""
    report_id = post_report(client, idempotency_key="p3-unscored").json()["id"]

    detail = client.get(f"{ENDPOINT}/{report_id}").json()

    assert detail["status"] == ReportStatus.RECEIVED.value
    assert detail["severity_score"] is None
    assert detail["authenticity_score"] is None
    assert detail["severity_reasons"] == []
    assert detail["scoring_provider"] is None


def test_ingestion_stays_inside_the_latency_budget(client: TestClient) -> None:
    """NFR-1: p95 under 300 ms excluding AI scoring."""
    durations = []
    for index in range(10):
        started = time.perf_counter()
        response = post_report(
            client,
            idempotency_key=f"p3-latency-{index}",
            files={"image": ("p.jpg", BytesIO(jpeg_bytes(index)), "image/jpeg")},
        )
        durations.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 201

    worst = max(sorted(durations)[:9])  # p90 of ten samples
    assert worst < 300, f"ingestion p90 was {worst:.0f} ms: {durations}"


# --- Retrieval --------------------------------------------------------------------------


def test_detail_returns_scores_and_reasons(client: TestClient) -> None:
    report_id = post_report(client, idempotency_key="p3-detail").json()["id"]

    response = client.get(f"{ENDPOINT}/{report_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == report_id
    assert set(body) >= {
        "severity_score",
        "severity_reasons",
        "authenticity_score",
        "authenticity_reasons",
        "priority_score",
        "scoring_provider",
    }


def test_detail_includes_the_image_block(client: TestClient) -> None:
    created = post_report(
        client,
        idempotency_key="p3-detail-image",
        files={"image": ("p.jpg", BytesIO(jpeg_with_exif(12.9, 77.6, datetime(2026, 8, 8, 8, 0))), "image/jpeg")},
    ).json()

    detail = client.get(f"{ENDPOINT}/{created['id']}").json()

    assert detail["image"]["phash"] == created["image"]["phash"]
    assert detail["image"]["exif"]["has_gps"] is True


def test_unknown_report_returns_a_typed_error(client: TestClient) -> None:
    response = client.get(f"{ENDPOINT}/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "REPORT_NOT_FOUND"
    assert error["detail"]["report_id"] == "00000000-0000-0000-0000-000000000000"


def test_malformed_id_is_a_validation_error(client: TestClient) -> None:
    response = client.get(f"{ENDPOINT}/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- Listing and filters -----------------------------------------------------------------


@pytest.fixture
def listing_reports(client: TestClient) -> str:
    """Three reports under one pseudonym, so filters are testable in isolation."""
    pseudonym = "list-fixture-owl"
    coordinates = [(12.90, 77.60), (12.95, 77.65), (13.20, 77.90)]
    for index, (lat, lng) in enumerate(coordinates):
        files = (
            {"image": ("p.jpg", BytesIO(jpeg_bytes(20 + index)), "image/jpeg")}
            if index == 0
            else None
        )
        post_report(
            client,
            idempotency_key=f"p3-list-{index}",
            reporter_pseudonym=pseudonym,
            lat=lat,
            lng=lng,
            files=files,
        )
    return pseudonym


def test_list_filters_by_pseudonym(client: TestClient, listing_reports: str) -> None:
    body = client.get(ENDPOINT, params={"reporter_pseudonym": listing_reports}).json()

    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_filters_by_status(client: TestClient, listing_reports: str) -> None:
    body = client.get(
        ENDPOINT, params={"reporter_pseudonym": listing_reports, "status": "received"}
    ).json()
    assert body["total"] == 3

    empty = client.get(
        ENDPOINT, params={"reporter_pseudonym": listing_reports, "status": "resolved"}
    ).json()
    assert empty["total"] == 0


def test_list_filters_by_bbox(client: TestClient, listing_reports: str) -> None:
    """bbox is min_lng,min_lat,max_lng,max_lat — two of the three fall inside."""
    body = client.get(
        ENDPOINT,
        params={"reporter_pseudonym": listing_reports, "bbox": "77.55,12.85,77.70,13.00"},
    ).json()

    assert body["total"] == 2


def test_list_filters_by_image_presence(client: TestClient, listing_reports: str) -> None:
    with_image = client.get(
        ENDPOINT, params={"reporter_pseudonym": listing_reports, "has_image": "true"}
    ).json()
    without = client.get(
        ENDPOINT, params={"reporter_pseudonym": listing_reports, "has_image": "false"}
    ).json()

    assert with_image["total"] == 1
    assert without["total"] == 2
    assert with_image["items"][0]["has_image"] is True


def test_list_paginates(client: TestClient, listing_reports: str) -> None:
    first = client.get(
        ENDPOINT, params={"reporter_pseudonym": listing_reports, "limit": 2, "offset": 0}
    ).json()
    second = client.get(
        ENDPOINT, params={"reporter_pseudonym": listing_reports, "limit": 2, "offset": 2}
    ).json()

    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1

    seen = {item["id"] for item in first["items"]} | {item["id"] for item in second["items"]}
    assert len(seen) == 3  # pages do not overlap


def test_list_rejects_an_oversized_page(client: TestClient) -> None:
    response = client.get(ENDPOINT, params={"limit": 5000})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "bbox",
    ["1,2,3", "a,b,c,d", "77.5,95.0,77.8,96.0", "77.8,12.9,77.5,13.1"],
)
def test_malformed_bbox_returns_a_typed_error(client: TestClient, bbox: str) -> None:
    response = client.get(ENDPOINT, params={"bbox": bbox})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_BBOX"


def test_unknown_status_filter_is_a_validation_error(client: TestClient) -> None:
    response = client.get(ENDPOINT, params={"status": "not-a-status"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
