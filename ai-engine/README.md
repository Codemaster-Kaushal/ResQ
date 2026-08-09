# RescueNet AI Engine

**Person 1 — AI/ML Layer | Phases 0–6**

Offline-first, IBM Granite-powered disaster-incident triage engine.
Classifies incident type, extracts structured risk factors, calculates severity,
analyzes images for visual signals, and evaluates report authenticity —
all without internet access at runtime.

---

## What It Does

| Phase | Capability |
|---|---|
| 0 | Shared Pydantic contracts (IncidentAIInput / IncidentAIOutput) |
| 1 | IBM Granite via Ollama — offline AI provider architecture |
| 2 | Incident classification into 8 fixed categories (FR-6) |
| 3 | Severity scoring 0–100 with human-readable reason codes (FR-7, FR-8) |
| 4 | Multimodal image analysis + text/image fusion |
| 5 | Authenticity + trust engine — deterministic, no LLM (FR-12, FR-13, FR-14) |
| 6 | Unified pipeline, `/ai/analyze` endpoint, governance + performance |

---

## Architecture

```
POST /ai/analyze  (Phase 6 — primary endpoint)
       |
       v
 TriagePipeline
       |
       +-> Classifier        -> GraniteLocalProvider -> Ollama -> IBM Granite
       |        |                                        (timeout)
       |        +-> RuleBasedClassifier (fallback)
       |
       +-> RiskExtractor     -> GraniteLocalProvider -> Ollama -> IBM Granite
       |              |                                 (timeout)
       |              +-> RuleBasedRiskExtractor (fallback)
       |
       +-> SeverityEngine    (deterministic maths)
       |
       +-> ImageAnalyzer     -> GraniteVisionProvider -> Ollama -> vision model
       |                                                  (VISION_UNAVAILABLE if none)
       |
       +-> FusionEngine      (weighted text + image combination)
       |
       +-> AuthenticityEngine (deterministic trust scoring)
               |
               +-> ImageHashService  (pHash duplicate detection)
               +-> validate_coordinates()
               +-> check_timestamp()
               +-> MovementChecker
               +-> CorroborationService
```

---

## Installation

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) installed and running locally

### 1. Clone and set up

```bash
git clone <repo>
cd rescuenet-ai

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env if needed — defaults work for standard Ollama installs
```

---

## Ollama Setup

```bash
# Install Ollama from https://ollama.com
ollama serve

# Text model (required for classification + severity):
ollama pull granite3.3:8b

# Vision model (optional — enables Phase 4 image analysis):
ollama pull llava

# Verify:
ollama list
```

---

## Environment Variables

### Core (Phases 0–3)

| Variable | Default | Description |
|---|---|---|
| `GRANITE_MODEL` | `granite3.3:8b` | Ollama model tag |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama base URL |
| `AI_TIMEOUT_SECONDS` | `5` | Inference timeout (FR-10) |
| `AI_TEMPERATURE` | `0.0` | Inference temperature |
| `SEVERITY_CRITICAL_THRESHOLD` | `80` | Critical severity cutoff |
| `SEVERITY_HIGH_THRESHOLD` | `60` | High severity cutoff |
| `SEVERITY_MEDIUM_THRESHOLD` | `40` | Medium severity cutoff |

### Phase 4 — Vision + Fusion

| Variable | Default | Description |
|---|---|---|
| `VISION_MODEL` | `""` | Vision model tag (empty = auto-detect) |
| `TEXT_FUSION_WEIGHT` | `0.60` | Text weight in fusion |
| `IMAGE_FUSION_WEIGHT` | `0.40` | Image weight in fusion |
| `MAX_IMAGE_SIZE_MB` | `10.0` | Maximum image size |

### Phase 5 — Authenticity

| Variable | Default | Description |
|---|---|---|
| `AUTHENTICITY_VERIFIED_THRESHOLD` | `90` | Min score for VERIFIED |
| `AUTHENTICITY_LIKELY_VALID_THRESHOLD` | `70` | Min score for LIKELY_VALID |
| `AUTHENTICITY_REVIEW_THRESHOLD` | `50` | Below = review_required |
| `CORROBORATION_RADIUS_METERS` | `500` | Corroboration search radius |
| `CORROBORATION_TIME_WINDOW_MINUTES` | `15` | Corroboration time window |
| `MAX_CLOCK_SKEW_MINUTES` | `15` | Max timestamp difference |
| `MAX_PLAUSIBLE_SPEED_KMH` | `300` | Max reporter speed |
| `IMAGE_DUPLICATE_HASH_DISTANCE` | `8` | pHash Hamming distance threshold |

---

## Running Locally

```bash
# Terminal 1 — Ollama
ollama serve

# Terminal 2 — AI Engine
uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

---

## API Endpoints

### `POST /ai/analyze` _(Phase 6 — primary endpoint)_

Full pipeline: classification → risk → severity → image fusion → authenticity.

```bash
curl -X POST http://localhost:8000/ai/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "report_id": "RPT-001",
    "description": "Five people trapped in flooded building",
    "latitude": 12.9716,
    "longitude": 77.5946,
    "client_timestamp": "2026-08-08T18:30:00Z",
    "reporter_pseudonym": "USER-A7F2"
  }'
```

```json
{
  "report_id": "RPT-001",
  "incident_type": "trapped_persons",
  "severity_score": 75,
  "severity_label": "HIGH",
  "severity_reason_codes": ["TRAPPED_PERSONS", "FLOODING"],
  "scoring_provider": "rule_based",
  "fallback_state": "RULE_BASED",
  "image_analysis": null,
  "multimodal_mode": "TEXT_ONLY",
  "authenticity": {
    "authenticity_score": 75,
    "verification_status": "LIKELY_VALID",
    "review_required": false,
    "authenticity_reason_codes": ["NO_IMAGE_SUBMITTED", "COORDINATES_VALID", "TIMESTAMP_PLAUSIBLE"]
  },
  "pipeline_version": "4.0"
}
```

### `POST /ai/triage` _(Phases 0–3 — unchanged)_

Original triage endpoint — backward compatible.

### `POST /ai/authenticity` _(Phase 5)_

Standalone authenticity check without full triage.

### `GET /ai/provenance` _(updated in Phase 6)_

Returns model identity, vision provider status, all thresholds, pipeline version.

### `POST /ai/classify`, `POST /ai/severity`

Slim single-purpose endpoints — unchanged from Phases 0–3.

---

## Authenticity Scoring

Deterministic 0–100 trust score. No LLM involved.

| Signal | Weight | Description |
|---|---|---|
| Image Originality | 25 pts | pHash duplicate detection |
| Geo Validity | 20 pts | Coordinate range + GPS check |
| Timestamp Plausibility | 20 pts | Client/server clock skew |
| Movement Plausibility | 15 pts | Physical speed between reports |
| Corroboration | 20 pts | Independent nearby reporters |

| Score | Status | review_required |
|---|---|---|
| 90–100 | VERIFIED | false |
| 70–89 | LIKELY_VALID | false |
| 50–69 | NEEDS_REVIEW | true |
| 0–49 | FLAGGED | true |

---

## Benchmark Results

```
Reports processed : 50
Throughput        : ~6,000 reports/sec
p50 latency       : ~0.15 ms
p95 latency       : ~0.19 ms
SLA (p95 <= 500ms): PASS
```

Run: `python scripts/benchmark_triage.py`

---

## Test Commands

```bash
# Run all tests (246 total)
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=ai_engine --cov=shared --cov=app --cov-report=term-missing

# Run benchmark
python scripts/benchmark_triage.py
```

---

## Fallback Behavior

| Condition | Result |
|---|---|
| Granite normal response | `scoring_provider: local_granite`, `fallback_state: NORMAL` |
| Granite timeout | `scoring_provider: rule_based`, `fallback_state: AI_BACKFILL_PENDING` |
| Granite unavailable | `scoring_provider: rule_based`, `fallback_state: RULE_BASED` |
| Vision model unavailable | `image_analysis.vision_available: false`, `multimodal_mode: TEXT_ONLY_FALLBACK` |

---

## Fixed Incident Taxonomy (FR-6)

| Value | Description |
|---|---|
| `structural_collapse` | Building, wall, or roof collapse |
| `flooding` | Flood water, inundation, rising water |
| `medical` | Injury, unconscious persons, medical emergency |
| `trapped_persons` | People unable to evacuate independently |
| `fire` | Active fire, flames, or smoke |
| `landslide` | Landslide, mudslide, slope collapse |
| `infrastructure` | Bridge, road, power, or utility damage |
| `other` | No matching category |

---

## Integration Contract for Person 2

**Primary endpoint:** `POST /ai/analyze` — returns `FullAnalysisResult`.
**Legacy endpoint:** `POST /ai/triage` — returns `IncidentAIOutput` (unchanged).

Both accept the same `IncidentAIInput`. All Phase 4–5 fields on
`IncidentAIOutput` are `Optional` with `None` defaults — **zero breaking
changes** to existing integrations.

### Error format (NFR-5)

```json
{
  "error": {
    "code": "AI_PROVIDER_UNAVAILABLE",
    "message": "...",
    "retryable": true
  }
}
```

---

## Documentation

| Document | Contents |
|---|---|
| `docs/PHASE_4_MULTIMODAL.md` | Vision providers, fusion, EXIF, config |
| `docs/PHASE_5_AUTHENTICITY.md` | Trust scoring, duplicate detection, corroboration |
| `docs/PHASE_6_GOVERNANCE.md` | Unified pipeline, endpoints, benchmark, test coverage |

---

## Docker

```bash
docker-compose up --build
```

---

## Known Limitations

1. **Vision:** Requires a separately pulled Ollama vision model (e.g., `ollama pull llava`). Without it, image analysis returns `VISION_UNAVAILABLE` gracefully.
2. **Corroboration store:** In-memory for Phase 5. Person 2 injects a DB-backed `CorroborationService` for production.
3. **Hash store:** In-memory for Phase 5. Person 2 provides persistent storage by passing a `known_hashes` dict backed by the database.
4. **Model cold start:** First inference after `ollama serve` may be slower than `AI_TIMEOUT_SECONDS`. Consider a warm-up request.
