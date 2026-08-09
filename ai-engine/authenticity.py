"""
RescueNet AI — Phase 7: Authenticity & Trust Engine
====================================================
Standalone flat-file module.  No external APIs.  No database.
State is persisted to state.json in the working directory.

Public API
----------
analyze_authenticity(
    report_id,
    image_path=None,
    lat=None,
    lon=None,
    client_ts=None,      # datetime or ISO-8601 string; None => skip time check
    server_ts=None,      # datetime or ISO-8601 string; None => use utcnow()
    pseudonym=None,
    incident_type=None,
) -> dict

Returns:
    {
        "score":        int   0-100,
        "band":         str   VERIFIED | LIKELY_VALID | NEEDS_REVIEW | FLAGGED,
        "reason_codes": list[str],
    }

On any internal error returns:
    { "score": 50, "band": "NEEDS_REVIEW", "reason_codes": ["AUTHENTICITY_ENGINE_ERROR"] }

Low-authenticity reports are NEVER deleted or rejected.
FLAGGED means "route to human review".
"""

import io
import json
import logging
import math
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration (all overrideable via env vars) ─────────────────────────────

PHASH_DUPLICATE_DISTANCE: int = int(os.environ.get("PHASH_DUPLICATE_DISTANCE", "8"))
MAX_TIME_SKEW_MINUTES: int    = int(os.environ.get("MAX_TIME_SKEW_MINUTES", "10"))
MAX_SPEED_KMH: float          = float(os.environ.get("MAX_SPEED_KMH", "300"))
CORR_RADIUS_M: float          = float(os.environ.get("CORR_RADIUS_M", "500"))
CORR_TIME_WINDOW_MIN: int     = int(os.environ.get("CORR_TIME_WINDOW_MIN", "15"))

STATE_FILE: str = os.environ.get("AUTHENTICITY_STATE_FILE", "state.json")

# ── Scoring weights (additive, applied to BASE_SCORE = 70) ───────────────────

BASE_SCORE: int = 70

WEIGHTS: dict[str, int] = {
    "IMAGE_EXACT_DUPLICATE":  -50,   # severe — exact copy always FLAGGED
    "IMAGE_NEAR_DUPLICATE":   -35,
    "IMAGE_NOT_DUPLICATE":    +10,
    "IMAGE_CHECK_FAILED":       0,
    "GEO_INVALID":            -25,
    "GEO_VALID":               +5,
    "TIME_IMPLAUSIBLE":       -20,
    "TIME_PLAUSIBLE":          +5,
    "IMPOSSIBLE_MOVEMENT":    -30,
    "CORROBORATED":           +20,
    "WEAK_CORROBORATION":      +8,
}

# ── Band thresholds ───────────────────────────────────────────────────────────

def _band(score: int) -> str:
    if score >= 80:
        return "VERIFIED"
    if score >= 60:
        return "LIKELY_VALID"
    if score >= 40:
        return "NEEDS_REVIEW"
    return "FLAGGED"


# ══════════════════════════════════════════════════════════════════════════════
# STATE  (image_hashes + recent_reports, persisted to state.json)
# ══════════════════════════════════════════════════════════════════════════════

def _load_state() -> dict:
    """
    Load state from STATE_FILE.
    Returns empty state on any error — never crashes the caller.
    """
    empty = {"image_hashes": [], "recent_reports": []}
    try:
        path = Path(STATE_FILE)
        if not path.exists():
            return empty
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return empty
        data = json.loads(raw)
        # Validate structure
        if not isinstance(data, dict):
            return empty
        if not isinstance(data.get("image_hashes", []), list):
            data["image_hashes"] = []
        if not isinstance(data.get("recent_reports", []), list):
            data["recent_reports"] = []
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        logger.warning("state.json is corrupt or unreadable — starting with empty state.")
        return empty


def _save_state(state: dict) -> None:
    """
    Persist state to STATE_FILE.
    Silently swallows I/O errors — we must never crash the caller.
    """
    try:
        Path(STATE_FILE).write_text(
            json.dumps(state, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Could not write state.json: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# 1.  IMAGE DUPLICATE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def check_image(image_path: Optional[str], report_id: str) -> list[str]:
    """
    Compute pHash for image_path and compare against stored hashes in state.json.

    Returns a list containing exactly one of:
        IMAGE_EXACT_DUPLICATE   — Hamming distance == 0
        IMAGE_NEAR_DUPLICATE    — 0 < distance <= PHASH_DUPLICATE_DISTANCE
        IMAGE_NOT_DUPLICATE     — distance > threshold (hash stored for future checks)
        IMAGE_CHECK_FAILED      — any processing error
        []                      — image_path is None (no image submitted)
    """
    if image_path is None:
        return []

    try:
        import imagehash
        from PIL import Image

        img = Image.open(image_path)
        new_hash = str(imagehash.phash(img))
        img.close()
    except Exception as exc:
        logger.warning("check_image: failed to hash %s — %s", image_path, exc)
        return ["IMAGE_CHECK_FAILED"]

    state = _load_state()
    hashes = state["image_hashes"]  # list of {"report_id": ..., "phash": ...}

    best_distance: Optional[int] = None
    best_match: Optional[str] = None

    try:
        import imagehash as _ih
        new_ih = _ih.hex_to_hash(new_hash)

        for entry in hashes:
            try:
                stored_ih = _ih.hex_to_hash(entry["phash"])
                dist = int(new_ih - stored_ih)
                if best_distance is None or dist < best_distance:
                    best_distance = dist
                    best_match = entry.get("report_id", "unknown")
            except Exception:
                continue
    except Exception as exc:
        logger.warning("check_image: comparison failed — %s", exc)
        return ["IMAGE_CHECK_FAILED"]

    if best_distance is not None and best_distance == 0:
        logger.info(
            "Exact duplicate: report=%s matched=%s distance=0", report_id, best_match
        )
        return ["IMAGE_EXACT_DUPLICATE"]

    if best_distance is not None and best_distance <= PHASH_DUPLICATE_DISTANCE:
        logger.info(
            "Near-duplicate: report=%s matched=%s distance=%d",
            report_id, best_match, best_distance,
        )
        return ["IMAGE_NEAR_DUPLICATE"]

    # Original — store the hash
    state["image_hashes"].append({"report_id": report_id, "phash": new_hash})
    _save_state(state)
    logger.info("Image stored: report=%s phash=%s", report_id, new_hash)
    return ["IMAGE_NOT_DUPLICATE"]


# ══════════════════════════════════════════════════════════════════════════════
# 2.  GEO + TIME + MOVEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres."""
    R = 6_371_000.0
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dr = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dr / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_dt(ts) -> Optional[datetime]:
    """Coerce a datetime or ISO-8601 string to an aware UTC datetime, or None."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    if isinstance(ts, str):
        try:
            # Python 3.11+ handles Z; earlier needs manual fix
            clean = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            logger.warning("Could not parse timestamp: %r", ts)
            return None
    return None


def check_geo_time(
    lat,
    lon,
    client_ts,
    server_ts,
    pseudonym: Optional[str],
) -> list[str]:
    """
    Validate coordinates, check timestamp plausibility, and detect impossible movement.

    Returns a list of applicable reason codes.
    Never raises.
    """
    codes: list[str] = []

    # ── Coordinate validation ─────────────────────────────────────────────────
    geo_valid = False
    if lat is None or lon is None:
        codes.append("GEO_INVALID")
    elif not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        codes.append("GEO_INVALID")
    elif lat == 0.0 and lon == 0.0:
        codes.append("GEO_INVALID")
    else:
        codes.append("GEO_VALID")
        geo_valid = True

    # ── Timestamp plausibility ────────────────────────────────────────────────
    client_dt = _parse_dt(client_ts)
    server_dt = _parse_dt(server_ts) or client_dt or datetime.now(tz=timezone.utc)

    if client_dt is not None:
        skew_minutes = abs((client_dt - server_dt).total_seconds()) / 60.0
        if skew_minutes > MAX_TIME_SKEW_MINUTES:
            codes.append("TIME_IMPLAUSIBLE")
        else:
            codes.append("TIME_PLAUSIBLE")

    # ── Impossible movement ───────────────────────────────────────────────────
    if pseudonym and geo_valid and client_dt is not None:
        state = _load_state()
        recent = state.get("recent_reports", [])

        # Find the most recent prior report from this pseudonym
        prev = None
        prev_dt = None
        for r in reversed(recent):
            if r.get("pseudonym") == pseudonym and r.get("report_id") is not None:
                prev = r
                prev_dt = _parse_dt(r.get("timestamp"))
                if prev_dt is not None:
                    break

        if prev is not None and prev_dt is not None:
            try:
                dist_m = _haversine_m(
                    float(prev["lat"]), float(prev["lon"]),
                    float(lat), float(lon),
                )
                elapsed_s = (client_dt - prev_dt).total_seconds()

                if elapsed_s <= 0 and dist_m > 0:
                    codes.append("IMPOSSIBLE_MOVEMENT")
                elif elapsed_s > 0:
                    speed_kmh = (dist_m / 1000.0) / (elapsed_s / 3600.0)
                    if speed_kmh > MAX_SPEED_KMH:
                        logger.warning(
                            "Impossible movement: pseudonym=%s speed=%.1f km/h",
                            pseudonym, speed_kmh,
                        )
                        codes.append("IMPOSSIBLE_MOVEMENT")
            except Exception as exc:
                logger.warning("Movement check failed: %s", exc)

    return codes


# ══════════════════════════════════════════════════════════════════════════════
# 3.  CORROBORATION
# ══════════════════════════════════════════════════════════════════════════════

def check_corroboration(
    lat,
    lon,
    timestamp,
    incident_type: Optional[str],
) -> list[str]:
    """
    Search recent_reports in state.json for independent reports of the same
    incident type within CORR_RADIUS_M metres and CORR_TIME_WINDOW_MIN minutes.

    Returns:
        ["CORROBORATED", "NEARBY_REPORTS_<n>"]  if count >= 2
        ["WEAK_CORROBORATION"]                   if count == 1
        []                                       if count == 0
    Never raises.
    """
    if lat is None or lon is None or timestamp is None:
        return []

    ref_dt = _parse_dt(timestamp)
    if ref_dt is None:
        return []

    try:
        state = _load_state()
        recent = state.get("recent_reports", [])

        count = 0
        for r in recent:
            # Must match incident type
            if incident_type and r.get("incident_type") != incident_type:
                continue

            r_lat = r.get("lat")
            r_lon = r.get("lon")
            r_ts  = _parse_dt(r.get("timestamp"))

            if r_lat is None or r_lon is None or r_ts is None:
                continue

            dist_m = _haversine_m(float(lat), float(lon), float(r_lat), float(r_lon))
            if dist_m > CORR_RADIUS_M:
                continue

            time_diff_min = abs((ref_dt - r_ts).total_seconds()) / 60.0
            if time_diff_min > CORR_TIME_WINDOW_MIN:
                continue

            count += 1

        if count >= 2:
            return ["CORROBORATED", f"NEARBY_REPORTS_{count}"]
        if count == 1:
            return ["WEAK_CORROBORATION"]
        return []

    except Exception as exc:
        logger.warning("check_corroboration failed: %s", exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 4.  SCORING
# ══════════════════════════════════════════════════════════════════════════════

def score_authenticity(codes: list[str]) -> tuple[int, str]:
    """
    Apply weighted adjustments to BASE_SCORE for each reason code.

    Returns:
        (score: int 0-100, band: str)
    """
    score = BASE_SCORE
    for code in codes:
        score += WEIGHTS.get(code, 0)
    score = max(0, min(100, score))
    return score, _band(score)


# ══════════════════════════════════════════════════════════════════════════════
# 5.  MASTER FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def analyze_authenticity(
    report_id: str,
    image_path: Optional[str] = None,
    lat=None,
    lon=None,
    client_ts=None,
    server_ts=None,
    pseudonym: Optional[str] = None,
    incident_type: Optional[str] = None,
) -> dict:
    """
    Run all authenticity checks and return a scored result.

    Args:
        report_id:     Unique report identifier (used for hash storage).
        image_path:    Path to image file, or None (no image = no duplicate check).
        lat:           Latitude float, or None.
        lon:           Longitude float, or None.
        client_ts:     Client timestamp (datetime or ISO-8601 str), or None.
        server_ts:     Server timestamp (datetime or ISO-8601 str), or None → utcnow().
        pseudonym:     Reporter pseudonym for movement check, or None.
        incident_type: Incident type string for corroboration, or None.

    Returns:
        {"score": int, "band": str, "reason_codes": list[str]}

    Never raises.  On internal failure returns NEEDS_REVIEW + AUTHENTICITY_ENGINE_ERROR.
    """
    try:
        all_codes: list[str] = []

        # 1. Image duplicate
        img_codes = check_image(image_path, report_id)
        all_codes.extend(img_codes)

        # 2. Geo + time + movement
        geo_codes = check_geo_time(lat, lon, client_ts, server_ts, pseudonym)
        all_codes.extend(geo_codes)

        # 3. Corroboration
        corr_codes = check_corroboration(lat, lon, client_ts, incident_type)
        all_codes.extend(corr_codes)

        # 4. Score
        score, band = score_authenticity(all_codes)

        # 5. Persist this report into recent_reports for future checks
        _persist_report(report_id, pseudonym, lat, lon, client_ts, incident_type)

        logger.info(
            "analyze_authenticity: report=%s score=%d band=%s codes=%s",
            report_id, score, band, all_codes,
        )

        return {"score": score, "band": band, "reason_codes": all_codes}

    except Exception as exc:
        logger.error("analyze_authenticity: unexpected error for %s — %s", report_id, exc, exc_info=True)
        return {
            "score": 50,
            "band": "NEEDS_REVIEW",
            "reason_codes": ["AUTHENTICITY_ENGINE_ERROR"],
        }


def _persist_report(
    report_id: str,
    pseudonym: Optional[str],
    lat,
    lon,
    timestamp,
    incident_type: Optional[str],
) -> None:
    """
    Add this report to recent_reports in state.json for future corroboration
    and movement checks.  Silently swallows all errors.
    """
    try:
        ts_str = None
        if timestamp is not None:
            dt = _parse_dt(timestamp)
            ts_str = dt.isoformat() if dt is not None else str(timestamp)

        state = _load_state()
        state["recent_reports"].append({
            "report_id":    report_id,
            "pseudonym":    pseudonym,
            "lat":          lat,
            "lon":          lon,
            "timestamp":    ts_str,
            "incident_type": incident_type,
        })
        _save_state(state)
    except Exception as exc:
        logger.warning("_persist_report failed: %s", exc)


# ── Convenience: reset state (useful for testing) ─────────────────────────────

def reset_state() -> None:
    """Clear state.json — primarily for tests and demos."""
    _save_state({"image_hashes": [], "recent_reports": []})
