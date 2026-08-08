# RescueNet AI — Backend

Severity-ordered, authenticity-verified emergency dispatch.

Emergency systems process reports in arrival order, so a sprained ankle reported at 09:00 is
actioned before a building collapse reported at 09:05. RescueNet replaces arrival-order dispatch
with **severity-ordered, authenticity-verified dispatch**, and records every state transition as a
process event so response bottlenecks are measurable rather than anecdotal.

**Team:** CtrlWin — Lakshya Arora, Taashu Sharma, Kaushal Choudhary
**Event:** Hackverse 2.0, MIT Bengaluru

Specs: [PRD](PRD-RescueNet-Backend.md) · [TRD](TRD-RescueNet-Backend.md)

---

## Quickstart

```bash
cd backend
./scripts/dev.sh
```

That creates the virtualenv, installs dependencies, copies `.env.example` to `.env` if needed, and
starts the API on <http://127.0.0.1:8000>.

| URL | What |
|---|---|
| <http://127.0.0.1:8000/docs> | Interactive OpenAPI docs |
| <http://127.0.0.1:8000/health> | Liveness + database status |
| <http://127.0.0.1:8000/> | Service metadata |

Load the demo dataset (safe to re-run — it inserts only what is missing):

```bash
./scripts/seed.sh              # or: python -m seed.seed
./scripts/seed.sh --reset      # wipe seeded data and rebuild it
```

Run the tests:

```bash
./scripts/test.sh
```

### Manual setup

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
```

---

## Phase status

The build runs in ten sequential phases (TRD §7). A phase starts only once the previous phase's
acceptance criteria pass.

| Phase | Outcome | Status |
|---|---|---|
| 1 | Project skeleton, config, DB, health check | ✅ Done |
| 2 | Data model, migrations, seed dataset | ✅ Done |
| 3 | Report ingestion + media storage | ✅ Done |
| 4 | Triage engine (classification + severity) | ✅ Done |
| 5 | Authenticity and trust scoring | ⬜ Next |
| 6 | Priority queue with ageing and override | ⬜ |
| 7 | Dispatch and assignment engine | ⬜ |
| 8 | Responder status lifecycle | ⬜ |
| 9 | Process event log, cycle-time mining, CSV export | ⬜ |
| 10 | Offline batch sync, governance endpoint, hardening, deploy | ⬜ |

### Endpoints available today

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service metadata |
| GET | `/health` | Liveness; 503 when the database is unreachable |
| POST | `/api/reports` | Submit a report (multipart; image optional) |
| GET | `/api/reports` | Filterable list — status, type, bbox, pseudonym, image presence |
| GET | `/api/reports/{id}` | Report detail with scores, reasons, and EXIF |
| GET | `/api/_debug/*` | Deliberate-failure routes (debug routes only) |

`/api/_debug/*` is mounted only when `ENABLE_DEBUG_ROUTES=true`. Set it to `false` in production.

### Filing a report

```bash
curl -X POST http://127.0.0.1:8000/api/reports \
  -F "text=Bridge support has cracked, traffic still crossing" \
  -F "lat=12.9352" -F "lng=77.6245" \
  -F "idempotency_key=demo-1" \
  -F "image=@photo.jpg;type=image/jpeg"
```

Only `text`, `lat`, and `lng` are required. `client_created_at`, `reporter_pseudonym`, and
`idempotency_key` are filled in by the server when omitted — a report never requires an identity
(NFR-7). Re-posting a key you have already used returns the original report with `duplicate: true`
and HTTP 200, so a retry over a flaky connection is safe.

`bbox` uses GeoJSON axis order — `min_lng,min_lat,max_lng,max_lat`:

```bash
curl "http://127.0.0.1:8000/api/reports?bbox=77.58,13.02,77.61,13.05&has_image=true"
```

### How images are handled

Uploaded bytes are written **verbatim** — never re-encoded — so the EXIF that Phase 5's
authenticity check depends on survives intact (FR-5). The perceptual hash is computed from the
stored file, the same route Phase 5 will take.

Three things the upload path does not trust: the declared content type (the file's magic bytes
decide, and the stored extension follows the real format), the client-supplied filename (a
traversal vector — the name on disk is always generated), and the file size (capped by
`MAX_IMAGE_BYTES`). Corrupt or truncated images are rejected with `INVALID_IMAGE` before anything
is written.

Ingestion does image work inline because it is local, deterministic and takes single-digit
milliseconds, and Phase 5 needs the hash to exist the moment the row does. It is *AI scoring* that
ingestion must never block on (NFR-1) — reports land at status `received` and Phase 4 attaches
triage as a background task.

---

## Errors

Every failure returns the same envelope (TRD §6) — there is no path that leaks a stack trace to a
client (NFR-5):

```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "An internal error occurred. The incident has been logged.",
    "detail": { "path": "/api/_debug/boom", "request_id": "demo-trace-9" }
  }
}
```

Every request carries an `X-Request-ID` (supplied by the caller or generated), echoed on the
response — including on 500s — and attached to every log line for that request. Quote it to find
the matching server-side traceback.

---

## Configuration

All settings are env-driven; see [`backend/.env.example`](backend/.env.example) for the full list
with comments. Every scoring and dispatch threshold is a variable on purpose (TRD §9) — tuning the
system is a config change, never a code change.

Key ones:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./rescuenet.db` | SQLite for demo; Postgres-compatible |
| `LOG_FORMAT` | `json` | `json` or `console` |
| `MAX_IMAGE_BYTES` | `10485760` | Upload size cap (10 MiB) |
| `ALLOWED_IMAGE_TYPES` | `image/jpeg,image/png,image/webp` | Accepted upload types |
| `ENABLE_DEBUG_ROUTES` | `true` | Mounts `/api/_debug/*` |
| `AI_PROVIDER_ORDER` | `gemini,groq,local` | Fallback chain; `local` is always appended |
| `AUTHENTICITY_FLAG_THRESHOLD` | `40` | Below this, a report is flagged for human review |
| `DISPATCH_MAX_RADIUS_KM` | `25` | Responder search radius |

---

## Triage and severity

Every report is scored 0–100 and **every score is explained**. A score with no reason codes is a
failed requirement (FR-8), so each contributing component appends its own:

```
severity = clamp(incident_weight + life_risk + people_affected
                 + vulnerability + image_modifier, 0, 100)
```

```json
{
  "severity_score": 78,
  "incident_type": "trapped_persons",
  "scoring_provider": "local",
  "severity_reasons": [
    { "code": "INCIDENT_TRAPPED_PERSONS", "weight": 40, "source": "taxonomy" },
    { "code": "LIFE_RISK_TRAPPED",        "weight": 12, "source": "text" },
    { "code": "LIFE_RISK_NO_EXIT",        "weight":  8, "source": "text" },
    { "code": "PEOPLE_AFFECTED_6_20",     "weight": 12, "source": "text" },
    { "code": "VULNERABILITY_CHILDREN",   "weight":  6, "source": "text" }
  ]
}
```

The reason weights **sum to the score**, always. Capping and clamping append their own reasons
rather than silently swallowing the difference — otherwise "explainable" would be decorative. The
seed re-checks this invariant on every run.

### The provider chain

`AI_PROVIDER_ORDER` is tried in order, each with a timeout and one retry:

| Provider | Role |
|---|---|
| `gemini` | Google Gemini Flash — the only free tier with vision, so the only one that can judge an image |
| `groq` | Very fast, text only. Any visual modifier it returns is discarded as unfounded |
| `local` | Deterministic rules. No network, no key, always available — **always last, never optional** |

**The router never raises.** A rate limit, a timeout, a malformed response, or a bug inside a
provider all degrade to the local scorer rather than cost a report its score. With no API keys
configured the remote providers are skipped instantly, so the default setup runs entirely offline
and demo day never waits on a quota.

Providers extract *signals only* — never a score. Weighting lives in `services/triage.py`, so the
arithmetic is identical whichever provider answered and swapping models cannot quietly change how
severe an incident is judged to be. Terms a model invents are dropped: an unknown term has no
defensible weight, and inventing one would put an unexplainable number into the score.

**Ingestion never blocks on scoring.** The POST response returns with status `received`; triage
runs afterwards as a background task and advances the report to `classified`. Anything left
unscored stays visible to `triage_pending()`, which is the retry queue FR-4 promises.

---

## The demo dataset

`seed/` builds 40 reports across 4 Bengaluru zones (Koramangala, Whitefield, Hebbal, Jayanagar)
and 8 responders. Twelve reports are **deliberate fixtures** — each one exists so a later phase's
acceptance criterion has something real to detect:

| Fixture | Exists so that |
|---|---|
| `dup-image-a` / `dup-image-b` | Two reporters forward one photograph → Phase 5 `DUPLICATE_IMAGE` |
| `stale-timestamp` | Client clock 8 h before receipt → Phase 5 `STALE_REPORT` |
| `corroborated-1/2/3` | Three independent reports, 254 m and 18 min apart → Phase 5 `CORROBORATED` |
| `null-island` | Coordinates (0, 0) → Phase 5 `GEO_IMPLAUSIBLE` |
| `impossible-move-a/b` | One pseudonym, 851 km apart, 6 min → Phase 5 `IMPOSSIBLE_MOVEMENT` |
| `low-information` | Text "help" → Phase 5 `LOW_INFORMATION` |
| `latest-critical` | Worst incident, newest timestamp → Phase 6 severity must beat FIFO |
| `aged-low-severity` | Minor incident waiting 4 h → Phase 6 ageing prevents starvation |

Responder placement is equally deliberate: `Structural Crew Echo` is the *nearest* unit to the
Koramangala incidents but has the wrong skill, `Medical Unit Hotel` starts at capacity, and
`Rescue Team Golf` starts offline — so Phase 7's candidate filter has real cases to exclude.

Two properties the seed guarantees, and re-checks on every run:

- **Idempotent.** Every row's UUID is derived from its fixture key, so a second run finds the same
  primary keys and inserts nothing.
- **Self-verifying.** After writing, it re-measures its own fixtures — pHash distance of the
  duplicate pair, the corroboration radius and window, the stale gap — and exits non-zero if any
  no longer holds. A seed that has quietly stopped exercising a later phase is worse than no seed.

Images are generated procedurally rather than committed as binaries, so the repository stays
text-only and the perceptual hashes are identical on every machine.

**Scores are never seeded directly.** The seed writes raw reports, then runs them through the same
triage path ingestion uses, so a green seed run is real evidence the engine works. Authenticity and
priority stay empty until Phases 5 and 6 fill them the same way.

Verified offline: with the network namespace disconnected *and* API keys configured — so Gemini and
Groq are genuinely attempted and genuinely fail — a full re-seed still scores all 40 reports in
about three seconds, every one with `scoring_provider: "local"`.

---

## Layout

```
backend/
├─ app/
│  ├─ main.py        App factory, request middleware, lifespan
│  ├─ config.py      Env-driven settings (TRD §9)
│  ├─ db.py          Engine, session dependency, health probe
│  ├─ api/           Routers
│  ├─ core/          Error envelope, logging, time, geo
│  ├─ models/        Report, Responder, Assignment, ProcessEvent
│  ├─ schemas/       Request/response models
│  ├─ services/      media.py, triage.py; authenticity, priority, dispatch, mining to come
│  └─ ai/            base, local, gemini, groq, prompt, parsing, router
├─ seed/             Idempotent, self-verifying demo data
│  ├─ seed.py
│  ├─ images.py      Procedural seed imagery
│  └─ fixtures/      Zones, responders, the 40 report specs
├─ tests/
└─ scripts/          dev.sh, test.sh, seed.sh
```

### Conventions worth knowing

- **All datetimes are naive UTC.** SQLite's DATETIME has no timezone slot, so a tz-aware value
  returns naive and comparisons blow up mid-calculation. `app/core/time.py` normalises at the
  boundary; client offsets are applied and dropped on the way in.
- **Enums store their lowercase value**, not the Python member name, so the database and the API
  JSON agree. See `app/models/columns.py`.
- **`ProcessEvent` is append-only** and is the single source of truth for lifecycle timings —
  `Assignment` deliberately does not duplicate the en-route/on-scene milestones.
- The model modules avoid `from __future__ import annotations`: SQLModel resolves relationships
  from runtime annotations and cannot follow deferred ones.

---

## Notes for this machine

Python 3.10.12 is what's installed here, so the code avoids 3.11-only syntax (`datetime.UTC`,
`StrEnum`). It runs unchanged on 3.11+.

If you have a ROS/cognipilot environment sourced, its `PYTHONPATH` entries are searched *before*
this project's virtualenv, and `pytest` will try to load ROS's `launch_testing` plugin and fail on
an unrelated missing dependency. `scripts/dev.sh` and `scripts/test.sh` both `unset PYTHONPATH` for
their own process, so use those. Nothing about your ROS setup is modified.

---

## Engineering rules (TRD §10)

- The AI router never raises. Ever.
- Ingestion never blocks on scoring.
- `ProcessEvent` is append-only.
- No auto-rejection of any report by any automated path — rejection is a human action, and is
  itself a logged event.
- No new dependencies after Phase 9.
- Commit at every passing acceptance criterion.
