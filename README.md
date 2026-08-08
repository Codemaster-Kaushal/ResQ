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
| 3 | Report ingestion + media storage | ⬜ Next |
| 4 | Triage engine (classification + severity) | ⬜ |
| 5 | Authenticity and trust scoring | ⬜ |
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
| GET | `/api/_debug/boom` | Raises an unhandled exception (debug routes only) |
| GET | `/api/_debug/app-error` | Raises a typed error (debug routes only) |
| GET | `/api/_debug/service-unavailable` | Raises a 503 (debug routes only) |

`/api/_debug/*` is mounted only when `ENABLE_DEBUG_ROUTES=true`. Set it to `false` in production.

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
| `ENABLE_DEBUG_ROUTES` | `true` | Mounts `/api/_debug/*` |
| `AI_PROVIDER_ORDER` | `gemini,groq,local` | Fallback chain; `local` is always appended |
| `AUTHENTICITY_FLAG_THRESHOLD` | `40` | Below this, a report is flagged for human review |
| `DISPATCH_MAX_RADIUS_KM` | `25` | Responder search radius |

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

**Reports are seeded unscored**, at status `received`. Phases 4 and 5 compute severity and
authenticity; pre-filling them would make those phases' acceptance criteria pass without the
engines doing any work.

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
│  ├─ schemas/       Request/response models    (Phase 3)
│  ├─ services/      Triage, authenticity, priority, dispatch, mining
│  └─ ai/            Providers + never-raising fallback router (Phase 4)
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
