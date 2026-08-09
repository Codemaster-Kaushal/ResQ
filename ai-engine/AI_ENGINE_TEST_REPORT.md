# ResQ AI — AI Engine Verification Report

**Report Generated:** 2025 (live run against http://127.0.0.1:8000)
**Test Scope:** Phases 1–22 of the QA + Integration verification specification

---

## Environment

| Item | Value |
|------|-------|
| OS | Windows 10 (10.0.26200) x64 |
| Python | 3.11.9 |
| FastAPI | 0.115.6 |
| Uvicorn | 0.34.0 |
| Ollama | Installed and running |
| Granite model | `granite3.3:8b` (4.9 GB — confirmed pulled) |
| Gemma model | `gemma4:latest` (9.6 GB — confirmed pulled) |
| API URL | http://127.0.0.1:8000 |
| Entrypoint | `uvicorn app.main:app` |
| Key packages | httpx 0.28.1, Pillow 10.4.0, ImageHash 4.3.2 |

---

## Test Summary

| # | Test | Result | Evidence |
|---|------|--------|----------|
| 1 | API Health | **PASS** | HTTP 200, `status:ok`, `provider:local_granite`, `model:granite3.3:8b`, `triage_model:granite3.3:8b`, `vision_model:gemma4:latest` |
| 2 | OpenAPI Contract | **PASS** | `/ai/analyze` POST exists; schema has `report_id`, `description`, `image`, `latitude`, `longitude`, `client_timestamp`, `reporter_pseudonym` |
| 3 | Text Classification | **PASS** | `TEST-TEXT-001` → `incident_type: flooding` (correct) |
| 4 | Granite Execution | **TIMEOUT** | Granite responds in ~15s on this CPU; `AI_TIMEOUT_SECONDS=5` causes every call to time out; all classifications use rule-based fallback |
| 5 | Severity | **PASS** | Score=79, band=HIGH, codes present (`MULTIPLE_PEOPLE_AT_RISK`, `TRAPPED_PERSONS`, `MEDICAL_EMERGENCY`, `ENVIRONMENTAL_DANGER`) |
| 6 | Severity Reasons | **PASS** | Multiple reason codes returned for all tested reports |
| 7 | Authenticity | **PASS** | Score returned (0–100), band returned (`VERIFIED`/`LIKELY_VALID`/`NEEDS_REVIEW`/`FLAGGED`) |
| 8 | Duplicate Detection | **PASS** | Same image submitted twice → `IMAGE_EXACT_DUPLICATE` in both; second → `FLAGGED` (score=20); first submission also flagged as exact dup due to prior state |
| 9 | GPS Validation | **PARTIAL PASS** | `lat=500` → HTTP 422 structured JSON (Pydantic rejects before engine); `lat=null` → `GEO_INVALID` in engine. No raw stack trace in either case. But `GEO_INVALID` is unreachable via the public `/ai/analyze` endpoint for the out-of-range case |
| 10 | Timestamp Validation | **FAIL** | `TIME_IMPLAUSIBLE` is **structurally impossible** via `/ai/analyze`. `IncidentAIInput` has no `server_timestamp` field; `analyze_report()` reads it from the dict but the schema never populates it; `check_geo_time()` then falls back to `client_dt` as `server_dt`, giving skew=0 always |
| 11 | Impossible Movement | **PASS** | Report A (Bengaluru 10:00) + Report B (Pune 10:05, same pseudonym) → `IMPOSSIBLE_MOVEMENT` detected; speed ~9,960 km/h >> 300 km/h threshold |
| 12 | Corroboration | **PASS** | 3 flooding reports within 500m and 15 minutes → all 3 get `CORROBORATED` + `NEARBY_REPORTS_3` |
| 13 | Text-only Degradation | **PASS** | image=null → API succeeds; no `IMAGE_REQUIRED` / `VISION_FAILURE` / HTTP 500; `multimodal_mode: TEXT_ONLY` |
| 14 | Gemma Vision | **FAIL** | Gemma4 responds (~67–97s), but returns `visual_confidence:0.0` and all signals `false` for both flood and fire images. Root cause: project images are **tiny synthetic placeholder PNGs (180–612 bytes)** — not real photographs. Both flood and fire give identical output → IMAGE_A == IMAGE_B |
| 15 | Multimodal Fusion | **FAIL** | When image is submitted, Gemma4 times out (120s timeout) or returns zero-signal response; `vision_provider: none` in provenance even after 130s wait; `multimodal_mode: TEXT_ONLY` reported despite image being present. Vision pipeline runs but produces no usable signals from placeholder images |
| 16 | Fallback | **PASS** | Ollama stopped → API still returns HTTP 200; `fallback_state: RULE_BASED`; `triage_provider: rule_based`; report not lost; no stack trace; latency ~2.2s |
| 17 | Provenance | **PARTIAL PASS** | `/ai/provenance` endpoint returns accurate metadata. `/ai/analyze` provenance shows `triage_provider: rule_based` (correct when fallback active) but shows `vision_provider: none` even when Gemma IS being called (vision runs but times out) |
| 18 | Latency | **FAIL** | 10 text-only requests: Min=5008ms, Max=5046ms, Avg=5026ms, P95=5046ms. All dominated by 5s Granite timeout wait. P95 target ≤5000ms: **FAIL**. Fallback-only latency ~2.2s (PASS if Granite excluded) |
| 19 | Dataset 2-run | **PASS** | `scripts/run_demo_dataset.py --runs 2`: all 12 records, all scenario checks PASS, run-to-run consistency PASS |
| 20 | Dataset 3-run | **PASS** | `scripts/run_demo_dataset.py --runs 3`: all 12 records, all scenario checks PASS, 3-run reproducibility PASS. `assert_demo_dataset.py`: Scenario assertions PASS, Three-run reproducibility PASS |
| 21 | State Isolation | **PASS** | `state.json` unchanged after all dataset runs; dataset uses `data/demo_state.json` in isolation |
| 22 | Response Contract | **PASS** | All required fields present: `report_id`, `incident_type`, `severity.{score,band,reason_codes}`, `authenticity.{score,reason_codes}`, `verification_status`, `confidence`, `provenance`, `thresholds`, `latency_ms`, `fallback_state`, `multimodal_mode` |

---

## AI Model Status

### Granite (`granite3.3:8b`)
**Status: TIMEOUT / FALLBACK**

- Model is pulled and present in Ollama ✓
- Ollama can serve it successfully (direct call returned `Ok` in ~15s) ✓
- **BUT:** `AI_TIMEOUT_SECONDS=5` in config means **every single API call times out**
- On this CPU, granite3.3:8b cold-load + inference takes ~15 seconds
- Result: 100% of `/ai/analyze` requests use `rule_based` fallback with `AI_BACKFILL_PENDING`
- Live Granite inference was **never successfully used** during API-level testing

### Gemma (`gemma4:latest`)
**Status: TIMEOUT / NOT VERIFIED FOR IMAGE CONTENT**

- Model is pulled and present in Ollama ✓
- Ollama can serve it (direct call returned in ~67–97s) ✓
- **BUT:** Even when it responds, it returns `visual_confidence:0.0` and all signals `false`
- Root cause: all project images are tiny synthetic placeholder PNGs (180–612 bytes), not real photographs
- `flood_genuine.png` = 416 bytes; `fire_scene.png` = 180 bytes
- Flood image and fire image produce **identical** Gemma output — vision is not differentiating images
- In the API pipeline: `VISION_TIMEOUT_SECONDS=120` is theoretically sufficient, but the response is useless
- `multimodal_mode` reports `TEXT_AND_IMAGE` only when vision completes AND `vision_available=True`; actual API responses show `TEXT_ONLY` (vision timed out or produced VISION_UNAVAILABLE due to race with Granite model load)
- **GEMMA VISION TEST = FAIL** (identical flood/fire output, zero confidence, placeholder images)

---

## Critical Findings

### BLOCKER 1 — Granite Always Times Out (`AI_TIMEOUT_SECONDS=5` vs ~15s inference time)
- **Impact:** 100% of text classification uses rule-based fallback. Granite is never live.
- **Evidence:** Every `/ai/analyze` request returns `fallback_state: AI_BACKFILL_PENDING`; server logs confirm `Ollama request timed out after 4.98s`
- **Root cause:** `granite3.3:8b` requires ~15s cold inference on this machine's CPU; timeout is set to 5s
- **Fix required:** Increase `AI_TIMEOUT_SECONDS` to 30–60 (or run on GPU hardware)

### BLOCKER 2 — `TIME_IMPLAUSIBLE` Is Unreachable Via `/ai/analyze`
- **Impact:** Timestamp plausibility check (Phase 12) cannot be triggered via the public API
- **Evidence:** `IncidentAIInput` schema has no `server_timestamp` field; `analyze_report()` reads `report.get("server_timestamp")` which is always `None`; `check_geo_time()` then computes `server_dt = client_dt` → skew=0 always
- **Fix required:** Add `server_timestamp: Optional[datetime]` to `IncidentAIInput`, or inject `server_timestamp` from the current time inside `analyze_report()`

### BLOCKER 3 — Demo/Project Images Are Synthetic Placeholders, Not Real Photos
- **Impact:** Gemma vision cannot be verified as working; multimodal fusion is untestable; both flood and fire images produce identical zero-confidence output
- **Evidence:** `flood_genuine.png`=416 bytes, `fire_scene.png`=180 bytes; Gemma returns `visual_confidence:0.0`, all signals `false` for both
- **Fix required:** Replace placeholder images with real disaster photographs in `data/images/` and `data/demo/images/`

### BLOCKER 4 — Latency P95 Exceeds 5000ms Target
- **Impact:** P95=5046ms (target ≤5000ms); all latency is Granite timeout wait overhead
- **Evidence:** 10-run test, min=5008ms, max=5046ms — every request hits the full 5s timeout
- **Fix required:** Directly dependent on BLOCKER 1 fix (increase timeout or GPU). Actual fallback-path latency is ~2.2s (PASS)

### FINDING 5 — Provenance Inaccuracy: `vision_provider` Misreported
- **Impact:** When vision runs but times out or fails, `vision_provider: none` is correct; but `/ai/provenance` endpoint always shows `vision_provider: local_gemma` regardless of actual execution state
- **Evidence:** `/ai/provenance` returns `vision_provider: local_gemma`, `vision_available: true` even when all image analyses are failing/timing out
- **Severity:** Medium — inaccurate observability metadata

### FINDING 6 — `GEO_INVALID` for Out-of-Range Lat/Lon Is Blocked by Pydantic Schema
- **Impact:** `lat=500` is rejected with HTTP 422 before reaching the authenticity engine; `GEO_INVALID` reason code can only be triggered by passing `null` lat/lon
- **Classification:** Design decision — Pydantic validation at API boundary is correct behavior, but the RPT-006 dataset scenario uses `lat=500` which works only in the offline dataset runner (bypasses Pydantic)
- **Severity:** Low — the dataset runner correctly tests the engine logic; the API boundary protection is actually correct

---

## Recommended Fixes (Priority Order)

| Priority | Fix | File(s) |
|----------|-----|---------|
| 1 (BLOCKER) | Increase `AI_TIMEOUT_SECONDS` from 5 to 30+ (or deploy on GPU) | `ai_engine/config.py` or `.env` |
| 2 (BLOCKER) | Add `server_timestamp` field to `IncidentAIInput` OR auto-inject `utcnow()` as `server_ts` in `analyze_report()` when field is absent | `shared/schemas/incident_ai.py`, `ai_engine/analyze.py` |
| 3 (BLOCKER) | Replace synthetic placeholder PNG files with real disaster photographs | `data/images/`, `data/demo/images/` |
| 4 (MEDIUM) | Fix `/ai/provenance` to check live vision availability accurately, not assume it | `app/routers/ai.py` |
| 5 (LOW) | Document that `GEO_INVALID` for out-of-range coordinates is caught by Pydantic at API boundary (not authenticity engine) — update API docs | `shared/schemas/incident_ai.py` |

---

## Detailed Test Evidence

### Phase 5 — Text-Only Classification
```
report_id: TEST-TEXT-001
incident_type: flooding ✓ (expected)
severity: 79 / HIGH ✓
reason_codes: MULTIPLE_PEOPLE_AT_RISK, TRAPPED_PERSONS, MEDICAL_EMERGENCY, ENVIRONMENTAL_DANGER ✓
authenticity: 100 / VERIFIED ✓
verification_status: VERIFIED ✓
fallback_state: AI_BACKFILL_PENDING (Granite timed out)
latency_ms: 5048
```

### Phase 8 — Gemma Vision Direct Test
```
Flood image (416 bytes):  {"flood_water": false, "fire_present": false, "structural_damage": false, "visual_confidence": 0.0}  — 97s
Fire image  (180 bytes):  {"flood_water": false, "fire_present": false, "structural_damage": false, "visual_confidence": 0.0}  — 67s
Result: IMAGE_A == IMAGE_B → GEMMA VISION TEST = FAIL
```

### Phase 10 — Duplicate Image Detection
```
TEST-DUP-P10-001 (first):  IMAGE_EXACT_DUPLICATE, GEO_VALID, TIME_PLAUSIBLE → FLAGGED (score=30)
TEST-DUP-P10-002 (second): IMAGE_EXACT_DUPLICATE, GEO_VALID, TIME_PLAUSIBLE, CORROBORATED → NEEDS_REVIEW (score=50)
Duplicate detection engine: WORKING ✓
```

### Phase 11 — GPS Validation
```
lat=500:   HTTP 422 (Pydantic schema enforcement) — structured JSON error, no stack trace ✓
lat=null:  GEO_INVALID in authenticity reason_codes ✓
```

### Phase 12 — Timestamp Validation
```
client_timestamp=2038 → TIME_PLAUSIBLE (WRONG — expected TIME_IMPLAUSIBLE)
Root cause: server_timestamp=None → server_dt=client_dt → skew=0 always
IncidentAIInput has no server_timestamp field → STRUCTURAL BUG
```

### Phase 13 — Impossible Movement
```
Report A: Bengaluru (12.9716, 77.5946) @ 10:00
Report B: Pune      (18.5204, 73.8567) @ 10:05, same pseudonym
Distance: ~830km in 5min → ~9,960 km/h >> 300 km/h threshold
Result: IMPOSSIBLE_MOVEMENT ✓
```

### Phase 14 — Corroboration
```
CORR-001 @ (12.9720, 77.5950) 10:30 → CORROBORATED, NEARBY_REPORTS_3 ✓
CORR-002 @ (12.9723, 77.5953) 10:32 → CORROBORATED, NEARBY_REPORTS_3 ✓
CORR-003 @ (12.9726, 77.5956) 10:34 → CORROBORATED, NEARBY_REPORTS_3 ✓
State persists within run ✓
```

### Phase 16 — Fallback
```
Ollama stopped → API HTTP 200 ✓
fallback_state: RULE_BASED ✓
triage_provider: rule_based ✓
Report not lost ✓
No stack trace ✓
Latency: 2.2s ✓
```

### Phase 18 — Latency (10 requests, text-only)
```
Min:  5008ms | Max: 5046ms | Avg: 5026ms | P95: 5046ms
ALL requests: fallback=AI_BACKFILL_PENDING (Granite timeout dominating)
Fallback-only latency (Ollama stopped): ~2200ms
Target P95 ≤ 5000ms: FAIL (by 46ms, caused entirely by Granite timeout overhead)
```

### Phase 19–20 — Dataset Reproducibility
```
--runs 2: 12 records, all 9 scenario checks PASS, run-to-run consistent ✓
--runs 3: 12 records, all 9 scenario checks PASS, 3-run reproducible ✓
assert_demo_dataset.py: Scenario assertions PASS, Three-run reproducibility PASS ✓
Note: All runs use deterministic fallback (Granite/Gemma timeouts)
```

### Phase 21 — Production State Safety
```
state.json before: 3 image_hashes, 36 recent_reports
state.json after:  3 image_hashes, 36 recent_reports (UNCHANGED) ✓
Dataset uses isolated: data/demo_state.json ✓
```

---

## AI ENGINE READY FOR BACKEND INTEGRATION: **NO**

### Blockers (must be resolved before integration):

1. **BLOCKER 1 — Granite always times out.** `AI_TIMEOUT_SECONDS=5` is insufficient for this hardware (~15s inference). Granite live classification is never executed during normal API operation. All requests run rule-based fallback. Increase timeout to ≥30s or provide GPU-class hardware.

2. **BLOCKER 2 — TIME_IMPLAUSIBLE is unreachable via the API.** The `server_timestamp` field is missing from `IncidentAIInput`. The timestamp plausibility check always computes skew=0, making `TIME_IMPLAUSIBLE` impossible to trigger through `/ai/analyze`. Add `server_timestamp` to the input schema or auto-inject from `utcnow()` inside `analyze_report()`.

3. **BLOCKER 3 — Real Gemma vision is unverifiable.** All project images are tiny synthetic placeholder PNGs (180–612 bytes). Gemma returns `visual_confidence:0.0` and identical all-false output for every image. Multimodal fusion cannot be confirmed as working. Replace with real disaster photographs.

### When blockers are resolved, the following are confirmed working:
- ✓ `/ai/health` responds correctly
- ✓ `/ai/analyze` endpoint is functional
- ✓ Rule-based classification fallback works correctly
- ✓ Severity scoring with reason codes works
- ✓ Authenticity engine works (duplicate detection, geo validation, impossible movement, corroboration)
- ✓ Fallback behavior is safe (no crashes, no data loss, no stack traces)
- ✓ Dataset reproducibility is confirmed (2-run and 3-run consistent)
- ✓ Production state is isolated from demo runs
- ✓ Full API response contract is present and structurally correct
