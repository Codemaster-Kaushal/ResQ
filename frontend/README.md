# RescueNet — Frontend

Two apps against the same backend, no build step:

| | Who | File |
|---|---|---|
| **Citizen PWA** | Someone reporting an emergency | [`index.html`](index.html) |
| **Control Room** | The operator deciding who gets help | [`control.html`](control.html) |

## Running it

```bash
cd backend && ./scripts/dev.sh     # API on :8000
cd frontend && ./serve.sh          # apps on :5173
```

Open <http://localhost:5173>. The frontend finds the backend automatically; if yours is elsewhere,
change it under **System → Change backend address** (stored per device).

To try it on a real handset, serve on your LAN and open `http://<your-ip>:5173` from the phone.
Geolocation and the camera need a secure context, so on a phone use `localhost` port-forwarding or
an HTTPS tunnel — over plain `http://<ip>` the browser will refuse both and the app falls back to
an approximate location.

---

## Everything on screen comes from the backend

No mock data anywhere. If the API cannot answer, the UI says so rather than showing a number that
looks real.

| Screen | Endpoint |
|---|---|
| Home status, stats, map, top of queue | `/health`, `/api/queue`, `/api/responders` |
| Report | `POST /api/reports` (multipart, EXIF preserved) |
| SOS | `POST /api/reports` with a maximum-urgency body |
| Nearby | `GET /api/reports` |
| Report detail | `GET /api/reports/{id}` |
| My reports | `GET /api/reports?reporter_pseudonym=…` |
| Responders | `GET /api/responders` |
| System | `GET /api/governance` |
| Control · Queue | `GET /api/queue`, `POST /api/queue/{id}/override` |
| Control · Review | `GET /api/reports?status=flagged`, `POST /api/reports/{id}/review` |
| Control · Dispatch | `POST /api/dispatch/assign` |
| Control · Bottlenecks | `GET /api/mining/bottlenecks` |
| Control · Process log | `GET /api/events`, `GET /api/events/export.csv` |
| Control · Governance | `GET /api/governance` |
| Offline sync | `POST /api/sync/reports` |

All sixteen endpoints are used. Nothing in the backend is unreachable from the UI.

---

## Offline is the point

G5 says reports must be accepted with no connectivity, and that only works if the client holds onto
them. [`js/store.js`](js/store.js) keeps an **IndexedDB outbox** — IndexedDB rather than
localStorage because a report can carry a photograph, and one 2 MB image base64-encodes past
localStorage's ~5 MB ceiling.

When the connection returns the outbox replays itself, split by whether a report has a photo:

- **text-only** → one batch to `POST /api/sync/reports`
- **with a photo** → individually through the multipart endpoint, since a JSON batch has nowhere to
  put the file

Both carry the same client-generated idempotency key, so a report cannot be created twice however
often a sync is retried, and a `duplicate` result counts as success — that is exactly what a
retried sync looks like.

The wait time survives too: `client_created_at` is stamped when the citizen writes the report, not
when it reaches the server, so a report filed offline keeps the ageing it accrued and does not lose
its place in the queue.

There is also a fallback the browser cannot tell you about: if `navigator.onLine` says online but
the backend is unreachable, the report is queued rather than lost.

---

## Built for a phone first

- Full-bleed on a handset with `100dvh` and `env(safe-area-inset-*)`, so the tab bar clears the home
  indicator and nothing hides under the notch. The phone frame you see on a desktop is presentation
  only, above 480 × 720.
- Every tap target is at least 44 px; verified at 320 px wide with no horizontal overflow.
- Inputs are 16 px, below which iOS zooms the whole page on focus.
- `prefers-reduced-motion` disables the screen transitions and the SOS pulse.
- Installable: web manifest, maskable icons, and a service worker that caches the app shell.
  **API responses are never cached** — a stale severity score is worse than an honest "cannot reach
  the server", and the outbox already covers the case that actually matters.

Leaflet is **vendored**, not loaded from a CDN, for the same reason the backend ships a local
scorer: the venue's network is allowed to die. Map *tiles* still need a connection, so a tile
failure degrades to a note rather than a blank grey square.

---

## What was dropped from the original mockup, and why

The design this was built from had four screens with no backend behind them. Rather than leave them
showing invented data, they were removed or repointed at something real:

| Was | Now |
|---|---|
| Family Safety Dashboard | **Removed.** No family or member model exists; every status on it was fabricated. |
| Local News feed | **Removed.** No news source, and social-media ingestion is an explicit non-goal (PRD §3). |
| Live Local Alerts (hard-coded Darjeeling/Mumbai cards) | **Repointed** at `GET /api/reports` — real incidents near you, sorted by distance and filterable by severity. |
| Emergency Contacts with invented distances | **Replaced** by the real responder roster with live load and availability. |
| Voice note capture | **Removed.** The backend accepts images only. |
| Hard-coded "Rescue Team Alpha · ETA 7 min" | **Replaced** by the real status timeline on each report. |
| Fake status bar (9:41, battery icons) | **Removed.** Mockup chrome; a real app shows the device's own. |

Added, because the backend supports them and the mockup had nothing for them: the offline outbox
and sync, severity and trust score breakdowns, the process timeline, the governance/transparency
screen, and the entire control room.

---

## Layout

```
frontend/
├─ index.html          Citizen PWA
├─ control.html        Control room
├─ css/
│  ├─ base.css         Tokens, shell, primitives (shared)
│  ├─ citizen.css
│  └─ control.css
├─ js/
│  ├─ api.js           Typed client for all 16 endpoints
│  ├─ store.js         Pseudonymous identity + IndexedDB outbox
│  ├─ geo.js           Location tracking and Leaflet maps
│  ├─ ui.js            DOM helpers, formatting, reason-code labels
│  ├─ citizen.js
│  └─ control.js
├─ assets/leaflet/     Vendored Leaflet 1.9.4
├─ icons/              Manifest icons
├─ manifest.webmanifest
├─ sw.js               App-shell cache
└─ serve.sh
```

Plain ES modules — no bundler, no framework, no `node_modules`. The app is a handful of screens and
a build step would cost more than it saves.

---

## Reading a score

The severity and trust breakdowns are the product's explanation of itself, so raw reason codes are
translated for citizens (`PEOPLE_AFFECTED_2_5` → "2–5 people affected") while the control room shows
the code *and* the plain-English gloss, because operators need the identifier to search the log.

The weights always add up to the score shown. If they ever do not, that is a bug in the backend, not
a rounding artefact in the UI.
