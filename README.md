# RescueNet AI — Backend

Severity-ordered, authenticity-verified emergency dispatch.

Emergency systems process reports in arrival order, so a sprained ankle reported at 09:00 is
actioned before a building collapse reported at 09:05. RescueNet replaces arrival-order dispatch
with **severity-ordered, authenticity-verified dispatch**, and records every state transition as a
process event so response bottlenecks are measurable rather than anecdotal.

**Team:** CtrlWin — Lakshya Arora, Taashu Sharma, Kaushal Choudhary
**Event:** Hackverse 2.0, MIT Bengaluru

Specs: [PRD](PRD-RescueNet-Backend.md) · [TRD](TRD-RescueNet-Backend.md) · Frontend: [frontend/README.md](frontend/README.md)

| Part | State |
|---|---|
| **Backend** — 16 endpoints, 509 tests, all ten TRD phases | Complete |
| **Frontend** — citizen PWA + control room, wired to every endpoint | Complete |
| **AI intelligence layer** — remote provider integration | Pending (Person 2); the local scorer runs in its place today |

---

## Quickstart

Two terminals — backend, then frontend:

```bash
cd backend  && ./scripts/dev.sh     # API   on http://127.0.0.1:8000
cd frontend && ./serve.sh           # apps  on http://127.0.0.1:5173
```

`dev.sh` creates the virtualenv, installs dependencies, copies `.env.example` to `.env` if needed,
and starts the API.

| URL | What |
|---|---|
| <http://127.0.0.1:5173/index.html> | **Citizen app** — report an emergency |
| <http://127.0.0.1:5173/control.html> | **Control room** — queue, dispatch, review, bottlenecks |
| <http://127.0.0.1:8000/docs> | Interactive OpenAPI docs |
| <http://127.0.0.1:8000/health> | Liveness + database status |

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

All ten phases are complete (TRD §7). Each was verified against its own acceptance criteria
before the next began.

| Phase | Outcome | Status |
|---|---|---|
| 1 | Project skeleton, config, DB, health check | ✅ Done |
| 2 | Data model, migrations, seed dataset | ✅ Done |
| 3 | Report ingestion + media storage | ✅ Done |
| 4 | Triage engine (classification + severity) | ✅ Done |
| 5 | Authenticity and trust scoring | ✅ Done |
| 6 | Priority queue with ageing and override | ✅ Done |
| 7 | Dispatch and assignment engine | ✅ Done |
| 8 | Responder status lifecycle | ✅ Done |
| 9 | Process event log, cycle-time mining, CSV export | ✅ Done |
| 10 | Offline batch sync, governance endpoint, hardening, deploy | ✅ Done |

### The API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service metadata |
| GET | `/health` | Liveness; 503 when the database is unreachable |
| POST | `/api/reports` | Submit a report (multipart; image optional) |
| GET | `/api/reports` | Filterable list — status, type, bbox, pseudonym, image presence |
| GET | `/api/reports/{id}` | Report detail with scores, reasons, and EXIF |
| POST | `/api/reports/{id}/review` | Human review of a flagged report — verify or reject |
| GET | `/api/queue` | The ordered priority queue, with each score's components |
| POST | `/api/queue/{id}/override` | Operator pin, demote, or clear — emits a process event |
| POST | `/api/dispatch/assign` | Match the top of the queue (or a named report) to a responder |
| POST | `/api/assignments/{id}/status` | Advance a case: acknowledged → en_route → on_scene → resolved → closed |
| POST | `/api/assignments/{id}/reject` | Decline an assignment; the report returns to the queue |
| GET | `/api/responders` | Roster with live load and availability |
| GET | `/api/events` | The append-only process event log |
| GET | `/api/events/export.csv` | Process-mining CSV export |
| GET | `/api/mining/bottlenecks` | Cycle times and flagged stages |
| POST | `/api/sync/reports` | Batch sync of offline-queued reports; idempotent |
| GET | `/api/governance` | Active provider, thresholds, human-in-the-loop record |
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
| `PRIORITY_WEIGHT_SEVERITY` | `0.70` | Severity's share of the priority score |
| `AGEING_RATE_PER_MINUTE` | `1.5` | How fast a waiting report climbs |
| `DISPATCH_MAX_RADIUS_KM` | `25` | Responder search radius |
| `DISPATCH_WEIGHT_DISTANCE` | `0.5` | Distance's share of the match score |

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

## Authenticity and trust

A second, independent score answers a different question: not *how bad is this*, but *should we
believe it*. Reports start at a baseline and evidence moves them:

| Signal | Adjustment |
|---|---|
| `DUPLICATE_IMAGE` — a photograph already on file | −45 |
| `IMPOSSIBLE_MOVEMENT` — one pseudonym, >100 km apart, within 10 min | −30 |
| `GEO_IMPLAUSIBLE` — invalid or null-island coordinates | −25 |
| `STALE_REPORT` — client clock >6 h before receipt | −15 |
| `LOW_INFORMATION` — under 5 tokens and no incident named | −10 |
| `CORROBORATED` — independent reports of the same event nearby | +25 |
| `EXIF_CONSISTENT` — the photo's own GPS agrees with the reported location | +10 |

Below `AUTHENTICITY_FLAG_THRESHOLD` a report becomes `flagged` and joins the review queue. As with
severity, the reason weights sum to the score.

### Nothing is ever auto-rejected

Automated scoring can lower a report's trust and route it to a human. It cannot reject, delete, or
hide one (FR-15). During a mass-casualty event the cost of silently discarding one true report
dwarfs the cost of reviewing a false one, so `rejected` is reachable only through
`POST /api/reports/{id}/review`, and every decision records the operator who made it:

```bash
curl -X POST http://127.0.0.1:8000/api/reports/$ID/review \
  -H 'Content-Type: application/json' \
  -d '{"decision":"reject","reviewer":"operator-priya","note":"Photo already filed by someone else"}'
```

A rejected report keeps its row and its text. Review records the decision without touching the
computed score — the operator overrides the routing, not the measurement.

### Two judgements the spec left open

- **The later report is penalised, not the original.** Duplicate detection compares against reports
  already on file, so whoever filed first is not punished for it. Ties on receipt time break on id,
  so a pair can never both be penalised.
- **Corroboration requires independence on three axes**: a different reporter, a different
  photograph, and the same incident type. FR-14 says independent reports of *the same event* — two
  unrelated incidents on one street corner do not make either more credible, and two reports built
  on one recycled photograph are one observation, not two. That last exclusion matters: without it,
  a duplicate could corroborate its own original, which is precisely the false-amplification this
  stage exists to stop. `CORROBORATION_REQUIRE_SAME_TYPE=false` reverts to pure proximity.

---

## The priority queue

```
priority = 0.70*severity + 0.15*authenticity + 0.15*ageing_bonus
ageing_bonus = min(100, minutes_waited * 1.5)
```

On the seeded dataset, `GET /api/queue` puts the report filed **one minute ago** ahead of one that
arrived **144 minutes** earlier:

| # | report | severity | trust | ageing | priority | waited |
|---|---|---|---|---|---|---|
| 1 | `latest-critical` | 94 | 70 | 1.8 | **76.58** | 1 min |
| 2 | `filler-14` | 60 | 85 | 99.0 | 69.61 | 66 min |
| 3 | `filler-01` | 64 | 60 | 100.0 | 68.80 | 144 min |

Every entry carries the three numbers behind its position, so an operator can argue with the
ranking rather than just accept it.

**Ageing uses the client clock, not receipt** (FR-28). A report filed offline an hour ago has been
waiting an hour, however slow the sync was — scoring by receipt time would punish exactly the
people with the worst connectivity. Ageing is also the starvation guarantee (FR-17): the seeded
`aged-low-severity` report has severity 14, but four hours of waiting lifts it from 18.8 to 33.8.

The order is recomputed on every read, because the ageing term moves with the clock. A queue cached
even for a minute starts lying about who has been waiting longest.

### Operator override

Operators have context the model does not, so their judgement outranks the arithmetic in both
directions:

```bash
curl -X POST http://127.0.0.1:8000/api/queue/$ID/override \
  -H 'Content-Type: application/json' \
  -d '{"action":"pin","operator":"controller-meera","reason":"Caller reports a child is missing"}'
```

`pin` sorts above every computed score, `demote` below every one, `clear` returns the report to the
computed band. The data model carries a single nullable integer for operator intent, so the sign
encodes direction — positive pins, negative demotes — and an explicit `rank` orders within a band.

Every override emits a `PRIORITY_OVERRIDDEN` process event recording the operator, the reason, and
the position it moved from, so the human-in-the-loop claim is evidenced in the log rather than
asserted in the pitch (FR-18, FR-30). This is the one event emitted so far; Phase 9 extends
emission to every transition and adds the log API, CSV export, and bottleneck mining.

---

## Dispatch

```
candidates  = available AND active_count < capacity AND within 25 km
match_score = 0.5*(1 - distance/25km) + 0.3*skill + 0.2*(1 - load)
```

The point is that **the best-fit responder is not the first free one** (G3). On the seeded data,
a medical call in Koramangala goes to `Medical Unit Alpha` at 0.551 km rather than
`Structural Crew Echo` 0.20 km *closer*:

| responder | skill | distance | score |
|---|---|---|---|
| **Medical Unit Alpha** | medical (exact) | 0.551 km | **0.9890** |
| Structural Crew Echo | structural (mismatch) | 0.352 km | 0.7530 |

Skill is a weighted tiebreaker, not a veto: 0.3 of skill cannot outrun 0.5 of distance across the
whole radius, so a matched unit 24 km away still loses to a compatible one next door.

Dispatching until the fleet is full assigns **13 reports across 8 responders and then defers**, with
nobody over capacity, `Medical Unit Hotel` (at capacity) and `Rescue Team Golf` (offline) never
offered, and all 23 remaining reports still in the queue:

```
12. assigned seed-filler-19  -> Structural Crew Foxtrot (2/2)
13. DEFERRED: No available responder within range has spare capacity
```

A deferred report **keeps its place and its accrued wait time** — nothing is dropped for want of a
crew. Two deliberate choices here:

- **Dispatch looks past an unplaceable head of queue.** If the worst report is somewhere no crew can
  reach, the next one down still gets help rather than a crew idling.
- **Only the head records a `DISPATCH_DEFERRED` event.** Logging one per report skipped would bury
  the deferral that matters under three dozen identical events.

---

## Responder lifecycle

```
assigned → acknowledged → en_route → on_scene → resolved → closed
     └──────────┴────────────┴──→ queued   (rejection, FR-21)
```

Only transitions the lifecycle permits are accepted. Skipping a step returns a typed error naming
what *is* allowed — a crew reporting "on scene" without ever acknowledging has almost certainly hit
the wrong button, and silently accepting it would corrupt the cycle times Phase 9 mines:

```json
{ "error": { "code": "ILLEGAL_TRANSITION",
             "message": "A report cannot move from assigned to resolved",
             "detail": { "allowed": ["acknowledged", "queued"] } } }
```

**Capacity is released at `resolved`, not `closed`.** The crew is free the moment they finish on
scene; holding their slot through the paperwork would idle a unit that could be dispatched. A unit
that was `busy` returns to `available` — but one an operator took `offline` stays offline, whatever
happens to its workload.

### Rejection preserves the wait

A declined report rejoins the queue **with the wait time it already accrued** — ageing runs from
when the citizen filed it, not from when a crew handed it back, so nobody is penalised for a
responder's unavailability:

```
rejected: assigned -> queued
  back in the queue at position 1
  wait time preserved: 66.7 minutes (not reset to 0)
```

The rejecting responder is then excluded from that report's candidate list. Without it the report
returns to the queue, matches the same best-fit crew again, and loops.

---

## Process intelligence

Every state transition is recorded, from intake to closure. One report's trail:

```
 1. REPORT_RECEIVED       reporter:anon-4a076758
 2. TRIAGE_COMPLETED      scorer:local
 3. AUTHENTICITY_SCORED   system
 4. REPORT_VERIFIED       system
 5. QUEUED                system
 6. ASSIGNED              operator:controller-meera
 7. ACKNOWLEDGED          responder-charlie
 8. EN_ROUTE              responder-charlie
 9. ON_SCENE              responder-charlie
10. RESOLVED              responder-charlie
11. CLOSED                responder-charlie
```

`GET /api/events/export.csv` emits exactly `case_id, activity, timestamp, resource` — Disco, ProM
and pm4py read that shape with no column mapping. The log streams in batches, so an export never
has to fit in memory.

### Where the process is actually slowing down

`GET /api/mining/bottlenecks` compares what each stage is taking now against the median it took
across completed cases:

```
FINDING: QUEUED→ASSIGNED
  9 completed cases took a median of 11.82 min.
  28 open cases are averaging 85.87 min — 7.26x.
  ACTION: Reports are waiting too long for a responder. Add crews, widen
          DISPATCH_MAX_RADIUS_KM, or check whether units are stuck at capacity.
```

That is the difference between "the response felt slow today" and a claim with a number attached.

Two things it deliberately does *not* do. A **median**, not a mean, so one nine-hour outlier cannot
hide a problem. And a stage with **no completed cases to compare against is reported but never
flagged** — the seeded review queue shows a 96-minute wait at `REPORT_FLAGGED` with a ratio of 0,
because no case has yet been through review. A long wait with no baseline is a fact, not a finding.

**A closed case's durations are the baseline, so the seed lays down a past** — it carries its oldest
reports through the full lifecycle with plausible backdated timings. Without that, a fresh database
can only honestly answer "no data".

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
| `filler-08` / `filler-10` | Photo EXIF GPS matching / not matching the report → `EXIF_CONSISTENT` |

The duplicate pair sits ~900 m from the other Koramangala reports on purpose: a fixture that
accidentally lands inside another report's corroboration radius tests two signals at once and stops
proving the one it was written for.

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
frontend/               Citizen PWA + control room — see frontend/README.md
backend/
├─ app/
│  ├─ main.py        App factory, request middleware, lifespan
│  ├─ config.py      Env-driven settings (TRD §9)
│  ├─ db.py          Engine, session dependency, health probe
│  ├─ api/           reports, queue, dispatch, assignments, responders, health
│  ├─ core/          Error envelope, logging, time, geo
│  ├─ models/        Report, Responder, Assignment, ProcessEvent
│  ├─ schemas/       Request/response models
│  ├─ services/      media, triage, authenticity, priority, dispatch, events, mining, pipeline
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

## Offline sync

A phone with no signal queues reports locally and posts the lot when it reconnects:

```bash
curl -X POST http://127.0.0.1:8000/api/sync/reports -H 'Content-Type: application/json' -d '{
  "device_id": "field-phone-12",
  "reports": [
    {"idempotency_key":"offline-1","text":"Landslide has blocked the ghat road, two vehicles trapped",
     "lat":13.0362,"lng":77.5985,"client_created_at":"2026-08-09T00:10:00+05:30"}
  ]
}'
```

Three properties make that safe:

- **Idempotent.** Dedup is by the client's own key, shared with the single-report endpoint, so a
  sync that timed out halfway is simply retried. The same batch twice creates nothing the second
  time.
- **Partial success.** One malformed report does not cost the other nineteen — each item gets its
  own outcome, and only the bad one is rejected. Coordinates are checked per item rather than by
  the body schema precisely so a single corrupt GPS fix cannot fail the batch.
- **The wait survives.** A report filed six hours ago arrives with six hours of ageing already
  accrued, and it *counts* — on the seeded dataset a synced report lands at queue position 3, not
  at the back:

```
#3   offline-2   severity 64  waited 360 min  ageing +100.0  priority 68.8
```

Images are not part of a batch; they use the single multipart endpoint.

---

## Governance — what this system is actually doing

TRD §9 names over-claiming in the pitch as a project risk. `GET /api/governance` is the mitigation:
it reports the provider that **really scored the reports**, read back out of the data rather than
from configuration, and says so in a sentence meant to be read aloud:

```
active provider : local   (running_on_fallback=True)
fallback state  : no remote provider credentials configured

provider   kind                             creds  avail  scored
gemini     remote language model            False  False       0
groq       remote language model            False  False       0
local      deterministic rule-based scorer  True   True       43

"All scoring is performed by the local deterministic rule-based scorer. No remote
 model credentials are configured, so no report has been scored by a language model.
 Image content is not analysed: the visual severity modifier is always zero."
```

It volunteers the limitation nobody would think to ask about. Add a Gemini key and the same
endpoint will say the remote calls are *failing* if they are, rather than letting a configured key
imply a model ran.

It also evidences the two claims that are easiest to assert and hardest to prove:

- **Human-in-the-loop** (FR-30) — overrides, reviews and assignment rejections, counted from the
  event log by operator. The total is the sum of those named categories, not of every
  operator-tagged event: a routine dispatch is a person pressing a button, but folding it in would
  overstate the claim.
- **Nothing auto-rejected** (FR-15) — `auto_rejected_reports` counts reports in `rejected` with no
  human review event behind them. It must always be `0`, and there is a test that fakes exactly
  that condition to prove the check has teeth.

---

## Deploying with Postgres

SQLite is the local default and stays that way — NFR-2 requires the API to run with **no network at
all**, which a hosted database would break. Postgres matters for the deployed instance, where free
tiers have ephemeral filesystems and a SQLite file would vanish on redeploy.

Nothing in the code needs changing. Paste the connection string in and go:

```bash
DATABASE_URL='postgresql://postgres:...@db.xxxx.supabase.co:5432/postgres'
```

`postgres://` and `postgresql://` are both rewritten to the psycopg 3 dialect automatically, since
SQLAlchemy would otherwise resolve them to psycopg2 — a driver this project doesn't ship.

The schema was built for this from Phase 2: enums are portable `VARCHAR` (no native `ENUM`, and no
`CHECK` either — both need a migration every time the taxonomy grows, so validity is enforced at
the application boundary instead), timestamps are `TIMESTAMP WITHOUT TIME ZONE` to match the
naive-UTC convention, and no SQLite-only SQL is used anywhere.
[`tests/test_postgres_compat.py`](backend/tests/test_postgres_compat.py) renders the whole schema
and compiles every live query against the Postgres dialect **offline**, so a portability break is
caught by the test suite rather than on deploy day.

### Deploying

[`render.yaml`](render.yaml) is a Render blueprint (New → Blueprint → point it at this repo);
[`backend/Dockerfile`](backend/Dockerfile) runs anywhere else. Both honour the platform's `$PORT`,
and the blueprint sets `ENABLE_DEBUG_ROUTES=false` — those routes raise on purpose.

Smoke-test whatever you deploy:

```bash
./backend/scripts/deploy-check.sh https://your-instance.onrender.com
```

It probes eleven endpoints, exits non-zero on the first failure, and prints the governance
summary so you can see at a glance which scorer the deployed instance is really using.

**Two caveats worth knowing before demo day.** Uploaded images live on the same ephemeral disk as
SQLite, so they need object storage to survive a redeploy. And Supabase's pooler on port 6543 wants
`prepare_threshold=0` with psycopg 3 — the direct connection on 5432 is simpler for a long-lived
server.

---

## Notes for this machine

Python 3.10.12 is what's installed here, so the code avoids 3.11-only syntax (`datetime.UTC`,
`StrEnum`). It runs unchanged on 3.11+.

If you have a ROS/cognipilot environment sourced, its `PYTHONPATH` entries are searched *before*
this project's virtualenv, and `pytest` will try to load ROS's `launch_testing` plugin and fail on
an unrelated missing dependency. `scripts/dev.sh` and `scripts/test.sh` both `unset PYTHONPATH` for
their own process, so use those. Nothing about your ROS setup is modified.

---

## Verification

Every phase was checked against its own acceptance criteria before the next one started, and the
whole suite is re-run on each change.

```
509 tests                    passing
509 tests, network disabled  passing   (NFR-2)
34 seed self-checks          passing
```

The seed is self-verifying: after writing the dataset it re-measures its own fixtures — the
duplicate pair's pHash distance, the corroboration radius and window, the stale gap, that the
newest critical report really does rank first — and exits non-zero if any of them stopped holding.
A seed that has quietly stopped exercising a phase is worse than no seed.

`scripts/test.sh`, `scripts/seed.sh` and `scripts/dev.sh` all `unset PYTHONPATH` first; see the
note on this machine's ROS environment below.

---

## Engineering rules (TRD §10)

- The AI router never raises. Ever.
- Ingestion never blocks on scoring.
- `ProcessEvent` is append-only.
- No auto-rejection of any report by any automated path — rejection is a human action, and is
  itself a logged event.
- No new dependencies after Phase 9.
- Commit at every passing acceptance criterion.
