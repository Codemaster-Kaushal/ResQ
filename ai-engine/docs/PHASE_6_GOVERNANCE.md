# Phase 6 — Governance, Performance + Integration Hardening

## Overview

Phase 6 integrates all Phase 4–5 capabilities into a unified pipeline,
adds the primary integration endpoint for Person 2, expands provenance
metadata, and validates performance via a benchmark script.

---

## Architecture

```
POST /ai/analyze
    ↓
TriagePipeline.run_full(incident)
    ├── TriageService.triage()          (Phases 0–3: classify + risk + severity)
    ├── ImageAnalyzer.analyze()         (Phase 4: preprocess + vision)
    ├── FusionEngine.fuse()             (Phase 4: text + image fusion)
    └── AuthenticityEngine.calculate_authenticity()  (Phase 5: trust scoring)
    ↓
FullAnalysisResult  (complete output for Person 2)
```

---

## Key Files

| File | Purpose |
|------|---------|
| `ai_engine/pipeline.py` | `TriagePipeline` — unified Phase 4–6 pipeline |
| `shared/schemas/incident_ai.py` | `FullAnalysisResult` — complete output model |
| `app/routers/ai.py` | `/ai/analyze`, `/ai/authenticity` endpoints |
| `scripts/benchmark_triage.py` | Performance benchmark |
| `data/demo/reports.json` | 50 synthetic demo reports |
| `data/demo/expected_results.json` | Expected classifications |
| `data/demo/images/` | Test images for vision + duplicate tests |

---

## New API Endpoints

### `POST /ai/analyze`

**Primary integration endpoint for Person 2.**

```json
// Request (same as /ai/triage)
{
  "report_id": "RPT-001",
  "description": "...",
  "image": null,
  "latitude": 12.9716,
  "longitude": 77.5946,
  "client_timestamp": "2026-08-08T18:30:00Z",
  "reporter_pseudonym": "USER-A1B2"
}

// Response (FullAnalysisResult)
{
  "report_id": "RPT-001",
  "incident_type": "flooding",
  "classification_confidence": 0.85,
  "classification_reason_codes": ["FLOOD_WATER_DETECTED"],
  "risk_factors": { ... },
  "severity_score": 52,
  "severity_label": "MEDIUM",
  "severity_reason_codes": ["FLOODING"],
  "scoring_provider": "rule_based",
  "fallback_state": "RULE_BASED",
  "image_analysis": null,
  "multimodal_mode": "TEXT_ONLY",
  "authenticity": {
    "authenticity_score": 75,
    "verification_status": "LIKELY_VALID",
    "review_required": false,
    "authenticity_reason_codes": ["NO_IMAGE_SUBMITTED", "COORDINATES_VALID", ...],
    "evidence": { ... }
  },
  "pipeline_version": "4.0"
}
```

### `POST /ai/authenticity`

Standalone authenticity check — no triage required.

### `GET /ai/provenance` (updated)

Now returns comprehensive metadata including:
- Vision provider status and model
- All authenticity thresholds
- Fusion weights
- Pipeline version and phases implemented

---

## Benchmark Results

| Metric | Value |
|--------|-------|
| Reports | 50 |
| Errors | 0 |
| Throughput | ~6,000 reports/sec |
| p50 latency | ~0.15 ms |
| p95 latency | ~0.19 ms |
| p99 latency | ~0.43 ms |
| SLA (p95 ≤ 500ms) | PASS |

Run yourself: `python scripts/benchmark_triage.py`

---

## Provider Abstractions

| Interface | File | Status |
|-----------|------|--------|
| `AIProvider` | `ai_engine/providers/base.py` | Implemented (Phases 1–3) |
| `VisionProvider` | `ai_engine/providers/vision_base.py` | Implemented (Phase 4) |
| `EmbeddingProvider` | `ai_engine/providers/embedding_base.py` | Defined, reserved for Phase 7+ |

---

## Test Coverage

| Test File | Coverage Area |
|-----------|---------------|
| `test_image_preprocessing.py` | Image validation, decoding, normalization |
| `test_vision.py` | Vision schemas, factories |
| `test_fusion.py` | FusionEngine all modes |
| `test_geo_validation.py` | Coordinate validation + haversine |
| `test_time_plausibility.py` | Timestamp skew detection |
| `test_movement.py` | Movement plausibility |
| `test_image_duplicate.py` | pHash duplicate detection |
| `test_corroboration.py` | Corroboration service |
| `test_authenticity.py` | AuthenticityEngine scoring |
| `test_provenance.py` | Provenance endpoint completeness |
| `test_fallback.py` | Graceful degradation scenarios |
| `test_full_pipeline.py` | TriagePipeline integration |
| `test_analyze_api.py` | /ai/analyze endpoint |

Total: **246 tests passing** (136 Phase 0–3 + 110 Phase 4–6)

---

## Backward Compatibility

- `/ai/triage` behavior is **unchanged**.
- `IncidentAIOutput` Phase 4–5 fields are all `Optional` with `None` defaults.
- Person 2's existing integration requires **no changes** to continue working.
- The new `/ai/analyze` endpoint is additive.

---

## Dependency Changes

```
# Added to requirements.txt
Pillow>=10.3.0    # image processing + EXIF
imagehash>=4.3.1  # perceptual hashing
```
