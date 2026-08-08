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
| 2 | Data model, migrations, seed dataset | ⬜ Next |
| 3 | Report ingestion + media storage | ⬜ |
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

## Layout

```
backend/
├─ app/
│  ├─ main.py        App factory, request middleware, lifespan
│  ├─ config.py      Env-driven settings (TRD §9)
│  ├─ db.py          Engine, session dependency, health probe
│  ├─ api/           Routers
│  ├─ core/          Error envelope, structured logging
│  ├─ models/        SQLModel tables            (Phase 2)
│  ├─ schemas/       Request/response models    (Phase 3)
│  ├─ services/      Triage, authenticity, priority, dispatch, mining
│  └─ ai/            Providers + never-raising fallback router (Phase 4)
├─ seed/             Idempotent demo data       (Phase 2)
├─ tests/
└─ scripts/          dev.sh, test.sh
```

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
