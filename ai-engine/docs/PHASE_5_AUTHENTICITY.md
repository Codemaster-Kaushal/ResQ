# Phase 5 — Authenticity + Trust Engine

## Overview

Phase 5 adds a fully deterministic trust scoring system to RescueNet AI.
Every submitted report receives an **authenticity score (0–100)** computed
from verifiable evidence signals — no LLM black-box involved.

The system **never deletes or rejects reports**. A low score sets
`review_required = True` for human review.

---

## Architecture

```
AuthenticityEngine.calculate_authenticity(...)
    │
    ├── 1. Image Originality  (25 pts)
    │       ImageHashService.check_duplicate()
    │       pHash (perceptual hash) — Hamming distance comparison
    │
    ├── 2. Geo Validity  (20 pts)
    │       validate_coordinates(lat, lon)
    │       Range check + (0,0) sentinel detection
    │
    ├── 3. Timestamp Plausibility  (20 pts)
    │       check_timestamp(client_ts, server_ts)
    │       Max clock skew = MAX_CLOCK_SKEW_MINUTES
    │
    ├── 4. Movement Plausibility  (15 pts)
    │       MovementChecker.check_movement(...)
    │       Haversine distance / time → required speed check
    │
    └── 5. Corroboration  (20 pts)
            CorroborationService.find_nearby_reports(...)
            Independent reporters within radius + time window
```

---

## Key Files

| File | Purpose |
|------|---------|
| `ai_engine/authenticity/schemas.py` | `AuthenticityResult`, `VerificationStatus`, `AuthenticityReasonCode` |
| `ai_engine/authenticity/authenticity_engine.py` | `AuthenticityEngine` — orchestrates all checks |
| `ai_engine/authenticity/image_duplicate.py` | `ImageHashService` — pHash duplicate detection |
| `ai_engine/authenticity/geo_check.py` | `validate_coordinates()`, `haversine_distance()` |
| `ai_engine/authenticity/time_check.py` | `check_timestamp()` |
| `ai_engine/authenticity/movement_check.py` | `MovementChecker` |
| `ai_engine/authenticity/corroboration.py` | `CorroborationService` — in-memory store |

---

## Scoring Weights

| Signal | Weight | Full Credit When |
|--------|--------|-----------------|
| Image Originality | 25 pts | No duplicate found |
| Geo Validity | 20 pts | Coordinates in valid range |
| Timestamp Plausibility | 20 pts | Skew ≤ MAX_CLOCK_SKEW_MINUTES |
| Movement Plausibility | 15 pts | Speed ≤ MAX_PLAUSIBLE_SPEED_KMH |
| Corroboration | 20 pts | ≥2 independent nearby reporters |

**Partial credits** are awarded for missing (not invalid) data.

---

## Verification Status

| Score Range | Status | review_required |
|-------------|--------|-----------------|
| 90–100 | `VERIFIED` | False |
| 70–89 | `LIKELY_VALID` | False |
| 50–69 | `NEEDS_REVIEW` | True |
| 0–49 | `FLAGGED` | True |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTHENTICITY_VERIFIED_THRESHOLD` | `90` | Min score for VERIFIED |
| `AUTHENTICITY_LIKELY_VALID_THRESHOLD` | `70` | Min score for LIKELY_VALID |
| `AUTHENTICITY_REVIEW_THRESHOLD` | `50` | Below this = review_required |
| `CORROBORATION_RADIUS_METERS` | `500` | Radius for corroboration search |
| `CORROBORATION_TIME_WINDOW_MINUTES` | `15` | Time window for corroboration |
| `MAX_CLOCK_SKEW_MINUTES` | `15` | Max acceptable timestamp difference |
| `MAX_PLAUSIBLE_SPEED_KMH` | `300` | Max reporter speed before flag |
| `IMAGE_DUPLICATE_HASH_DISTANCE` | `8` | pHash Hamming distance threshold |
| `IMAGE_ORIGINALITY_WEIGHT` | `25` | Scoring weight (pts) |
| `GEO_VALIDITY_WEIGHT` | `20` | Scoring weight (pts) |
| `TIME_PLAUSIBILITY_WEIGHT` | `20` | Scoring weight (pts) |
| `MOVEMENT_PLAUSIBILITY_WEIGHT` | `15` | Scoring weight (pts) |
| `CORROBORATION_WEIGHT` | `20` | Scoring weight (pts) |

---

## Image Duplicate Detection (FR-12)

Uses **pHash** (perceptual hashing via `imagehash` library):
- Exact duplicate: Hamming distance = 0 → 0 points
- Near duplicate: distance ≤ threshold → 30% credit, `IMAGE_NEAR_DUPLICATE`
- Original: distance > threshold → full 25 pts, `IMAGE_NOT_DUPLICATE`

The in-memory hash store is designed for Phase 5.
**Person 2** can replace it with a DB-backed implementation by subclassing
`ImageHashService` or injecting a custom `known_hashes` dict.

---

## Corroboration (FR-14)

The `CorroborationService` uses an in-memory report store.
**Person 2** can replace the backend by subclassing and overriding
`find_nearby_reports()` to use a database query.

Only **independent** reporters (different pseudonyms from the current reporter)
count toward the corroboration score.

---

## API Endpoints

- `POST /ai/analyze` — full pipeline including authenticity
- `POST /ai/authenticity` — standalone authenticity check only
- `GET /ai/provenance` — includes all authenticity thresholds

---

## Guarantees

1. **Deterministic** — same inputs always produce the same score.
2. **Never rejects** — `review_required=True` only (no deletion).
3. **No LLM** — 100% rule-based math.
4. **Privacy-preserving** — only pseudonymous reporter IDs used.
