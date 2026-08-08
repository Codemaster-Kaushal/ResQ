# RescueNet AI — Backend Technical Requirements Document (TRD)

**Version:** 1.0
**Companion to:** PRD-RescueNet-Backend.md
**Scope:** Backend implementation. Ten sequential phases with acceptance criteria.

---

## 1. Stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Best fit for scoring and image work |
| Framework | FastAPI | Auto-generated OpenAPI docs — free marks under "presentation" |
| ORM | SQLModel (SQLAlchemy + Pydantic) | One model definition for DB and API schema |
| Database | SQLite (dev/demo) → Postgres-compatible | Use `DATABASE_URL`; do not use SQLite-only SQL |
| Migrations | Alembic | Or `create_all` if time-pressed; do not block on this |
| Background work | FastAPI `BackgroundTasks` | No Celery, no Redis, no broker |
| Image hashing | `imagehash` + `Pillow` | pHash for duplicate detection |
| EXIF | `Pillow` / `piexif` | Geo-time consistency |
| AI (primary) | Google Gemini Flash — free tier | Only free option with vision |
| AI (secondary) | Groq — free tier | Text-only, very fast |
| AI (floor) | Local rule-based scorer | Always available, zero network |
| HTTP client | `httpx` | Async, with timeout |
| Testing | `pytest` | Scoring functions must be tested; they are the product |
| Deploy | Render / Railway free tier, or local | Have a URL early |

**Confirm current free-tier quotas before relying on them.** Provider terms change; the local
scorer exists precisely so this is not a demo-day risk.

---

## 2. Project layout

```
backend/
├─ app/
│  ├─ main.py                 App factory, router registration, exception handlers
│  ├─ config.py               Pydantic Settings, env-driven
│  ├─ db.py                   Engine, session dependency
│  ├─ models/
│  │  ├─ report.py
│  │  ├─ responder.py
│  │  ├─ assignment.py
│  │  └─ process_event.py
│  ├─ schemas/                Request/response Pydantic models
│  ├─ api/
│  │  ├─ reports.py
│  │  ├─ queue.py
│  │  ├─ dispatch.py
│  │  ├─ responders.py
│  │  ├─ events.py
│  │  ├─ sync.py
│  │  └─ governance.py
│  ├─ services/
│  │  ├─ triage.py            Classification + severity orchestration
│  │  ├─ authenticity.py      Trust scoring
│  │  ├─ priority.py          Queue ordering + ageing
│  │  ├─ dispatch.py          Responder matching
│  │  ├─ events.py            emit_event()
│  │  └─ mining.py            Cycle times + bottleneck detection
│  ├─ ai/
│  │  ├─ base.py              Provider protocol
│  │  ├─ gemini.py
│  │  ├─ groq.py
│  │  ├─ local.py             Deterministic scorer — the floor
│  │  └─ router.py            Provider selection + fallback chain
│  └─ core/
│     ├─ errors.py            Typed error envelope
│     ├─ geo.py               Haversine
│     └─ logging.py
├─ seed/
│  ├─ seed.py
│  └─ fixtures/               ~40 reports, 8 responders, 2 duplicate image pairs
├─ tests/
├─ .env.example
└─ requirements.txt
```

---

## 3. Data model

### `Report`
| Field | Type | Notes |
|---|---|---|
| id | UUID | PK |
| idempotency_key | str, unique, indexed | Client-generated; enables offline dedup |
| text | str | Free text |
| image_path | str, nullable | Local path or object key |
| image_phash | str, nullable, indexed | Perceptual hash |
| lat, lng | float | Validated ranges |
| client_created_at | datetime | Time on the reporter's device |
| received_at | datetime | Server receipt |
| reporter_pseudonym | str | No PII |
| incident_type | enum, nullable | Set by triage |
| severity_score | int, nullable | 0–100 |
| severity_reasons | JSON | List of reason codes |
| authenticity_score | int, nullable | 0–100 |
| authenticity_reasons | JSON | List of reason codes |
| status | enum | See lifecycle below |
| priority_score | float, nullable | Computed, refreshed on read |
| scoring_provider | str, nullable | `gemini` / `groq` / `local` |
| manual_override_rank | int, nullable | Operator pin |

**Status lifecycle:**
`received → classified → verified → queued → assigned → acknowledged → en_route → on_scene → resolved → closed`
Branch: `classified → flagged → (human review) → verified | rejected`

### `Responder`
`id, name, skill (medical|rescue|structural), lat, lng, capacity (int), active_count (int), status (available|busy|offline)`

### `Assignment`
`id, report_id, responder_id, assigned_at, acknowledged_at, resolved_at, rejected_at, rejection_reason`

### `ProcessEvent`
`id, case_id (= report_id), activity, resource, timestamp, metadata (JSON)`

Append-only. Never updated, never deleted.

---

## 4. Scoring specifications

### 4.1 Severity (0–100)

```
severity = clamp(
    incident_weight        # 0–40
  + life_risk_signal       # 0–30
  + people_affected        # 0–15
  + vulnerability_signal   # 0–15
, 0, 100)
```

| Component | Rule |
|---|---|
| `incident_weight` | Fixed table per incident type: trapped_persons 40, structural_collapse 38, medical 32, fire 30, flooding 26, landslide 26, infrastructure 14, other 10 |
| `life_risk_signal` | Keyword/LLM detection of: unconscious, not breathing, bleeding, trapped, drowning, rising water, no exit. Each hit adds, capped at 30 |
| `people_affected` | Parsed count. 1 → 3, 2–5 → 7, 6–20 → 12, 20+ → 15 |
| `vulnerability_signal` | Children, elderly, disabled, pregnant, injured. Capped at 15 |

Every non-zero component appends a reason code, e.g.
`{"code": "LIFE_RISK_TRAPPED", "weight": 12, "source": "text"}`.

**Image contribution:** when an image is present, the AI provider returns a
`visual_severity_modifier` in the range −10 to +10 which is added before clamping, with reason
code `IMAGE_CORROBORATION` or `IMAGE_CONTRADICTION`. Absent image → modifier 0, no failure.

### 4.2 Authenticity (0–100)

Baseline 60, adjusted:

| Signal | Adjustment |
|---|---|
| Near-duplicate image (pHash Hamming distance ≤ 8) of an existing report | −45, code `DUPLICATE_IMAGE` |
| Client timestamp more than 6 h before receipt | −15, code `STALE_REPORT` |
| Invalid or null-island coordinates | −25, code `GEO_IMPLAUSIBLE` |
| Same pseudonym reporting from > 100 km apart within 10 min | −30, code `IMPOSSIBLE_MOVEMENT` |
| ≥ 2 independent reports within 500 m and 30 min | +25, code `CORROBORATED` |
| EXIF GPS present and within 1 km of reported coords | +10, code `EXIF_CONSISTENT` |
| Text is < 5 tokens and contains no incident term | −10, code `LOW_INFORMATION` |

Threshold: `authenticity_score < 40` → status `flagged`, human review queue.
**Never auto-reject.** Rejection is a human action and is itself a logged event.

### 4.3 Priority score

```
priority = 0.70 * severity
         + 0.15 * authenticity
         + 0.15 * ageing_bonus

ageing_bonus = min(100, minutes_waiting_since_client_created_at * 1.5)
```

Ageing uses **client** timestamp, so an offline report filed an hour ago is not penalised for
syncing late. Manual override pin sorts above all computed scores.

### 4.4 Dispatch matching

Candidate filter: `status == available` AND `active_count < capacity` AND within 25 km.

```
match_score = 0.5 * distance_component     # 1 - (distance / 25km)
            + 0.3 * skill_component        # exact 1.0, compatible 0.6, mismatch 0.2
            + 0.2 * load_component         # 1 - (active_count / capacity)
```

Skill mapping: `medical → medical`, `trapped_persons|structural_collapse → rescue`,
`flooding|landslide|infrastructure → structural`, `fire → rescue`.
Highest `match_score` wins. No candidate → report stays queued, event `DISPATCH_DEFERRED` emitted.

### 4.5 Bottleneck detection

For each activity transition pair, compute median duration across all closed cases.
Flag any transition where the current open cases' mean wait exceeds `1.5 ×` the median.
Output: transition name, median, current, deviation ratio, and a suggested action string.

---

## 5. AI provider contract

```python
class ScoringProvider(Protocol):
    name: str
    async def classify(self, text: str, image_bytes: bytes | None) -> TriageResult: ...
```

`TriageResult`: `incident_type`, `life_risk_terms[]`, `people_affected_estimate`,
`vulnerability_terms[]`, `visual_severity_modifier`, `confidence`.

**Router behaviour:** try providers in configured order, each with a 4-second timeout and one
retry. On total failure, return the local scorer's result and set `scoring_provider = "local"`.
The router **never raises**. This is the single most important line in the codebase.

Providers must return strict JSON. Parse defensively — strip code fences, tolerate trailing text,
validate against the Pydantic model, fall through on validation failure.

---

## 6. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness + DB check |
| POST | `/api/reports` | Submit one report (multipart for image) |
| GET | `/api/reports/{id}` | Report detail with scores and reasons |
| GET | `/api/reports` | Filterable list (status, type, bbox) |
| POST | `/api/sync/reports` | Batch offline sync, idempotent |
| GET | `/api/queue` | Ordered priority queue |
| POST | `/api/queue/{id}/override` | Operator pin/demote |
| POST | `/api/reports/{id}/review` | Resolve a flagged report (verify or reject) |
| POST | `/api/dispatch/assign` | Assign top of queue, or a specific report |
| POST | `/api/assignments/{id}/status` | Responder lifecycle update |
| POST | `/api/assignments/{id}/reject` | Return to queue |
| GET | `/api/responders` | List with load and availability |
| GET | `/api/events` | Process event log (paginated) |
| GET | `/api/events/export.csv` | Process-mining CSV |
| GET | `/api/mining/bottlenecks` | Cycle times + flagged stages |
| GET | `/api/governance` | Active provider, thresholds, fallback state, override count |

**Error envelope (all failures):**
```json
{ "error": { "code": "REPORT_NOT_FOUND", "message": "...", "detail": {} } }
```

---

## 7. The ten phases

Each phase ends with a working, committed, demonstrable state. Do not start a phase until the
prior phase's acceptance criteria pass.

### Phase 1 — Foundations
Build: FastAPI app factory, `config.py` with env settings, DB engine and session dependency,
`/health`, structured logging, global exception handler with the typed error envelope, `.env.example`.
**Accept:** `uvicorn app.main:app` starts; `/health` returns DB status; `/docs` renders; a deliberate
unhandled exception returns the error envelope, not a stack trace.

### Phase 2 — Data model and seed
Build: all four models, relationships, indexes on `idempotency_key` and `image_phash`, migrations,
idempotent seed script with 40 reports across 4 zones, 8 responders, and **at least one deliberate
duplicate image pair** plus one stale-timestamp report.
**Accept:** `python -m seed.seed` runs twice with no duplicates and no errors; row counts assert correctly.

### Phase 3 — Report ingestion
Build: `POST /api/reports` (multipart), validation, image storage, EXIF extraction, pHash computation,
`GET /api/reports/{id}`, `GET /api/reports` with filters.
**Accept:** report with image persists, pHash computed, EXIF read; invalid coordinates rejected with a
typed error; missing image succeeds.

### Phase 4 — Triage engine
Build: `ai/base.py` protocol, `ai/local.py` deterministic scorer first, then `gemini.py` and `groq.py`,
then `router.py` with the fallback chain. `services/triage.py` implements §4.1. Wire as a background
task on ingestion.
**Accept:** every seeded report has a severity score and non-empty reasons; **disconnect the network
and re-seed — all reports still score**, with `scoring_provider = "local"`.

### Phase 5 — Authenticity
Build: `services/authenticity.py` implementing §4.2 — pHash duplicate lookup, geo-time checks,
corroboration window query, EXIF consistency. Flagging at threshold. `POST /api/reports/{id}/review`.
**Accept:** the seeded duplicate pair is caught; the stale report is penalised; a corroborated cluster
scores higher; nothing is auto-deleted.

### Phase 6 — Priority queue
Build: `services/priority.py` implementing §4.3, `GET /api/queue`, `POST /api/queue/{id}/override`.
**Accept:** a high-severity report seeded with the **latest** timestamp ranks first; a low-severity
report left waiting demonstrably climbs via ageing; an override pins and emits an event.

### Phase 7 — Dispatch engine
Build: `core/geo.py` haversine, `services/dispatch.py` implementing §4.4, `POST /api/dispatch/assign`,
capacity enforcement, `DISPATCH_DEFERRED` path.
**Accept:** assignment picks a responder that is closer *and* skill-matched over one that is merely
closest; capacity is never exceeded; no eligible responder leaves the report queued.

### Phase 8 — Responder lifecycle
Build: `POST /api/assignments/{id}/status` with legal transition enforcement, rejection returning to
queue with wait time preserved, `GET /api/responders` with live load.
**Accept:** full happy path to `resolved`; illegal transition returns a typed error; rejected report
retains its original ageing.

### Phase 9 — Process intelligence
Build: `emit_event()` called on **every** transition across all prior phases (audit this — it is
usually incomplete), `GET /api/events`, CSV export in `case_id, activity, timestamp, resource`
columns, `services/mining.py` implementing §4.5, `GET /api/mining/bottlenecks`.
**Accept:** a report taken end-to-end produces a complete, ordered event trail; CSV opens cleanly;
bottleneck endpoint returns a real finding from seeded data with a suggested action.

### Phase 10 — Sync, governance, hardening
Build: `POST /api/sync/reports` batch endpoint with idempotency-key dedup and client-timestamp
preservation; `GET /api/governance`; pytest coverage on `triage`, `authenticity`, `priority`,
`dispatch`, `mining`; README with one-command startup; deploy.
**Accept:** the same batch posted twice creates no duplicates; offline-filed reports keep their
original wait time; governance reports the true active provider; **full suite passes with the
network disabled**; deployed URL responds.

---

## 8. Testing priorities

Test the scoring functions above all else — they are the product, and they are also what judges
will interrogate. Minimum:

- Severity: each component contributes independently; clamping holds at both bounds
- Authenticity: duplicate detection at the pHash boundary; corroboration window edges
- Priority: ageing prevents starvation; override sorts above computed scores
- Dispatch: capacity limit; skill preference over pure proximity; no-candidate path
- Router: every provider failing still returns a valid result

---

## 9. Configuration

```
DATABASE_URL=sqlite:///./rescuenet.db
AI_PROVIDER_ORDER=gemini,groq,local
GEMINI_API_KEY=
GROQ_API_KEY=
AI_TIMEOUT_SECONDS=4
AUTHENTICITY_FLAG_THRESHOLD=40
PHASH_DUPLICATE_DISTANCE=8
CORROBORATION_RADIUS_M=500
CORROBORATION_WINDOW_MIN=30
DISPATCH_MAX_RADIUS_KM=25
BOTTLENECK_DEVIATION_RATIO=1.5
MEDIA_STORAGE_PATH=./media
```

Every threshold is config-driven. Judges ask "what if you tuned that?" — the answer should be
an env var, not a code change.

---

## 10. Engineering rules

- The AI router never raises. Ever.
- Ingestion never blocks on scoring.
- `ProcessEvent` is append-only.
- No auto-rejection of any report by any automated path.
- No new dependencies after Phase 9.
- Commit at every passing acceptance criterion.
- Seed data is demo infrastructure — treat migrations that touch it as high-risk.
