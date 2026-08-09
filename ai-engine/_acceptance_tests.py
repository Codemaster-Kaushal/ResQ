"""
Phase 7 Acceptance Tests
Run with:  python _acceptance_tests.py
"""

import io
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── helpers ───────────────────────────────────────────────────────────────────

def make_test_image(color=(30, 80, 180)):
    from PIL import Image
    img = Image.new("RGB", (64, 64), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def save_image(data, path):
    Path(path).write_bytes(data)

PASSED = []
FAILED = []

def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print("  PASS  " + name)
    else:
        FAILED.append(name)
        print("  FAIL  " + name + (": " + str(detail) if detail else ""))


# ===========================================================================
# STATE.JSON BEHAVIOUR
# ===========================================================================

from authenticity import _load_state, reset_state, STATE_FILE

print("")
print("=== STATE.JSON BEHAVIOR ===")

if Path(STATE_FILE).exists():
    Path(STATE_FILE).unlink()
s = _load_state()
check("missing file returns empty state", s == {"image_hashes": [], "recent_reports": []})

Path(STATE_FILE).write_text("", encoding="utf-8")
s = _load_state()
check("empty file returns empty state", s == {"image_hashes": [], "recent_reports": []})

Path(STATE_FILE).write_text("{bad{{", encoding="utf-8")
s = _load_state()
check("corrupt file returns empty state", s == {"image_hashes": [], "recent_reports": []})

reset_state()
check("reset_state writes valid JSON",
      json.loads(Path(STATE_FILE).read_text()) == {"image_hashes": [], "recent_reports": []})


# ===========================================================================
# TEST 1: Clean report
# ===========================================================================

from authenticity import analyze_authenticity

print("")
print("=== TEST 1: Clean report ===")
reset_state()
now = datetime.now(tz=timezone.utc)

r1 = analyze_authenticity(
    report_id="RPT-CLEAN-001",
    image_path=None,
    lat=12.9716,
    lon=77.5946,
    client_ts=now,
    server_ts=now,
    pseudonym="USER-T1",
    incident_type="flooding",
)
print("  score=" + str(r1["score"]) + "  band=" + r1["band"] + "  codes=" + str(r1["reason_codes"]))
check("clean report score >= 80",      r1["score"] >= 80, str(r1["score"]))
check("clean report band = VERIFIED",  r1["band"] == "VERIFIED", r1["band"])
check("GEO_VALID present",             "GEO_VALID" in r1["reason_codes"])
check("TIME_PLAUSIBLE present",        "TIME_PLAUSIBLE" in r1["reason_codes"])


# ===========================================================================
# TEST 2: Exact duplicate image
# ===========================================================================

print("")
print("=== TEST 2: Exact duplicate image ===")
reset_state()

img_bytes = make_test_image()
save_image(img_bytes, "_test_img_a.png")
save_image(img_bytes, "_test_img_b.png")

now = datetime.now(tz=timezone.utc)

r2a = analyze_authenticity(
    report_id="RPT-ORIG-001",
    image_path="_test_img_a.png",
    lat=12.9716, lon=77.5946,
    client_ts=now, server_ts=now,
    pseudonym="USER-T2A",
)
print("  [first]  score=" + str(r2a["score"]) + "  band=" + r2a["band"] + "  codes=" + str(r2a["reason_codes"]))
check("first image not duplicate", "IMAGE_NOT_DUPLICATE" in r2a["reason_codes"])

r2b = analyze_authenticity(
    report_id="RPT-DUP-002",
    image_path="_test_img_b.png",
    lat=12.9716, lon=77.5946,
    client_ts=now, server_ts=now,
    pseudonym="USER-T2B",
)
print("  [second] score=" + str(r2b["score"]) + "  band=" + r2b["band"] + "  codes=" + str(r2b["reason_codes"]))
check("second image exact duplicate", "IMAGE_EXACT_DUPLICATE" in r2b["reason_codes"])
check("duplicate band = FLAGGED",     r2b["band"] == "FLAGGED", r2b["band"])
check("duplicate score <= 39",        r2b["score"] <= 39, str(r2b["score"]))

for p in ["_test_img_a.png", "_test_img_b.png"]:
    Path(p).unlink(missing_ok=True)


# ===========================================================================
# TEST 3: Invalid coordinates
# ===========================================================================

print("")
print("=== TEST 3: Invalid coordinates ===")
reset_state()
now = datetime.now(tz=timezone.utc)

r3 = analyze_authenticity(
    report_id="RPT-BADGEO-001",
    image_path=None,
    lat=500,
    lon=77.5946,
    client_ts=now, server_ts=now,
    pseudonym="USER-T3",
)
print("  score=" + str(r3["score"]) + "  band=" + r3["band"] + "  codes=" + str(r3["reason_codes"]))
check("lat=500 gives GEO_INVALID", "GEO_INVALID" in r3["reason_codes"])

r3b = analyze_authenticity(
    report_id="RPT-ORIGIN-001",
    image_path=None,
    lat=0.0, lon=0.0,
    client_ts=now, server_ts=now,
    pseudonym="USER-T3B",
)
check("(0,0) gives GEO_INVALID", "GEO_INVALID" in r3b["reason_codes"])


# ===========================================================================
# TEST 4: Impossible movement
# ===========================================================================

print("")
print("=== TEST 4: Impossible movement ===")
reset_state()

t0 = datetime(2026, 8, 9, 10, 0, 0, tzinfo=timezone.utc)

r4a = analyze_authenticity(
    report_id="RPT-MOVE-001",
    image_path=None,
    lat=12.9716, lon=77.5946,   # Bengaluru
    client_ts=t0, server_ts=t0,
    pseudonym="USER-T4",
    incident_type="flooding",
)
print("  [Bengaluru] score=" + str(r4a["score"]) + "  band=" + r4a["band"] + "  codes=" + str(r4a["reason_codes"]))

t1 = t0 + timedelta(minutes=5)
r4b = analyze_authenticity(
    report_id="RPT-MOVE-002",
    image_path=None,
    lat=19.0760, lon=72.8777,   # Mumbai (~980 km away)
    client_ts=t1, server_ts=t1,
    pseudonym="USER-T4",        # same pseudonym
    incident_type="flooding",
)
print("  [Mumbai]    score=" + str(r4b["score"]) + "  band=" + r4b["band"] + "  codes=" + str(r4b["reason_codes"]))
check("impossible movement flagged",        "IMPOSSIBLE_MOVEMENT" in r4b["reason_codes"])
check("impossible movement reduces score",  r4b["score"] < r4a["score"], str(r4b["score"]) + " vs " + str(r4a["score"]))


# ===========================================================================
# TEST 5: Corroboration
# ===========================================================================

print("")
print("=== TEST 5: Corroboration ===")
reset_state()

base_time = datetime(2026, 8, 9, 10, 0, 0, tzinfo=timezone.utc)

rA = analyze_authenticity(
    report_id="RPT-CORR-001",
    lat=12.9716, lon=77.5946,
    client_ts=base_time, server_ts=base_time,
    pseudonym="USER-CA",
    incident_type="flooding",
)
print("  [A] score=" + str(rA["score"]) + "  band=" + rA["band"] + "  codes=" + str(rA["reason_codes"]))

rB = analyze_authenticity(
    report_id="RPT-CORR-002",
    lat=12.9722, lon=77.5950,   # ~90 m from A
    client_ts=base_time + timedelta(minutes=1),
    server_ts=base_time + timedelta(minutes=1),
    pseudonym="USER-CB",
    incident_type="flooding",
)
print("  [B] score=" + str(rB["score"]) + "  band=" + rB["band"] + "  codes=" + str(rB["reason_codes"]))

rC = analyze_authenticity(
    report_id="RPT-CORR-003",
    lat=12.9718, lon=77.5955,   # ~120 m from A
    client_ts=base_time + timedelta(minutes=2),
    server_ts=base_time + timedelta(minutes=2),
    pseudonym="USER-CC",
    incident_type="flooding",
)
print("  [C] score=" + str(rC["score"]) + "  band=" + rC["band"] + "  codes=" + str(rC["reason_codes"]))

check("C has CORROBORATED code",   "CORROBORATED" in rC["reason_codes"])
check("C has NEARBY_REPORTS_N",    any(c.startswith("NEARBY_REPORTS_") for c in rC["reason_codes"]))
check("C score > A score",         rC["score"] > rA["score"], str(rC["score"]) + " > " + str(rA["score"]))


# ===========================================================================
# SUMMARY
# ===========================================================================

total = len(PASSED) + len(FAILED)
print("")
print("=" * 60)
print("  RESULTS: " + str(len(PASSED)) + "/" + str(total) + " passed")
if FAILED:
    print("  FAILED : " + str(FAILED))
print("=" * 60)

if FAILED:
    sys.exit(1)
