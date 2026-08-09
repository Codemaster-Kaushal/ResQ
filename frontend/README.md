# ResQ AI — Frontend

> *AI that gets help where it matters.*

Two apps against one backend, no build step:

| | Who | File |
|---|---|---|
| **Citizen PWA** | Someone who needs help | [`index.html`](index.html) |
| **Control Room** | The operator deciding who gets it first | [`control.html`](control.html) |

They are deliberately **not** the same application with different buttons. The information models
are separate and do not overlap — see [Privacy](#privacy-what-a-citizen-can-never-see).

## Running it

```bash
cd backend && ./scripts/dev.sh     # API on :8000
cd frontend && ./serve.sh          # apps on :5173
```

Geolocation, camera and voice need a secure context. On a phone, use `localhost` port-forwarding or
an HTTPS tunnel — over plain `http://<ip>` the browser blocks all three and the app falls back to an
approximate location with voice hidden.

---

## Citizen app

Bottom navigation is unchanged: **Home · Nearby · SOS · My Reports · System**. Profile lives under
System rather than adding a sixth tab.

### Anonymous or account

First launch asks for a language, then how to continue. **Anonymous is the default path** — nobody
is made to create an account during a life-threatening emergency. Anonymous reports still get a
tracking code (`RQ-4B3BAB`).

"Account" means an emergency profile stored on this device. There is no password, because there is
no backend auth to authenticate against, and the onboarding screen says so rather than implying one.

### SOS

The existing SOS button is kept exactly as it was. What changed is the interaction:

```
SOS  →  HOLD TO ACTIVATE (2s)  →  SOS ACTIVE (review)  →  SEND SOS  →  outcome
```

Two seconds of hold prevents an accidental alert; a progress ring fills as you hold and the phone
vibrates on arm. The review step shows what is about to be sent — location, headcount, whether
medical and family information is attached — before anything leaves the device.

**The states are exact.** The app never says "sent" for something that has only reached local
storage:

| State | Means |
|---|---|
| `SOS SAVED — OFFLINE` | On this device only. **Has not reached anyone.** |
| `SOS SENT` | Accepted by the backend |
| `RESPONDER ASSIGNED` → `EN ROUTE` → `ARRIVED` | From the real report status |
| `HELP COMPLETED` | Resolved |

The offline result screen carries an explicit warning in the user's language, because a false sense
of safety here is the most dangerous thing this app could do.

### Track my help

Every report gets a tracking screen: current state in plain language, a timeline, the assigned
responder (team name, type, distance-derived ETA — never a phone number or a home location), and
exactly what was shared. Updates read as sentences — *"A rescue team has been assigned."* — not as
system events.

### Multilingual — including the part that usually gets faked

The UI translates fully into **English, हिन्दी, ಕನ್ನಡ, தமிழ், తెలుగు, മലയാളം**. Language names, not
flags.

Report *content* is the harder half. The backend's local scorer is English keyword-based (TRD §4.1),
so a Kannada report classifies as `other` and scores 10 — invisible. Rather than pretend otherwise,
the report screen pairs free text in any language with **language-independent signal chips** that
compose a normalised English summary. The original wording is always preserved and sent first.

Measured against the real backend:

| Report | Severity | Classified as |
|---|---|---|
| Kannada text alone | **10** | `other` — effectively invisible |
| \+ signal chips | **58** | `trapped_persons` |
| \+ emergency profile & family | **65** | `trapped_persons` |

Voice input uses the browser's own SpeechRecognition in all six languages. It needs a connection and
is unavailable in Firefox, so the button is hidden rather than shown broken.

### Emergency profile and family

Both are stored **on the device only** — the backend has no profile model, and adding one would mean
holding medical records server-side, which is not a decision to make casually.

When attached to a report, they are composed into the report body as plain English. That is not a
workaround for its own sake: the triage engine reads exactly these signals (vulnerability terms —
children, elderly, disabled, pregnant, injured), so attaching a profile **measurably raises the
severity score** for a household that genuinely needs more help. Both attachments are toggles, off
the moment you turn them off.

---

## Privacy: what a citizen can never see

| Citizen sees | Control room sees |
|---|---|
| Their own reports and SOS | Every incident |
| Their own status and responder | Verification and authenticity |
| Public safety warnings | AI reasoning, reason codes, confidence |
| General service availability | Queue depth, crew counts, priority scores |

Concretely, the citizen app no longer shows *"30 in queue"*, *"6/8 crews free"* or *"Top severity
94"* — those are operational facts. Home now shows **My active requests**, **Nearby safety** and a
coarse **Emergency services** band (`Available` / `Limited` / `None`) rather than crew numbers.

**Nearby** keeps its card design but changed what it contains. It no longer lists other people's
incidents. Reports are clustered into ~650 m cells and published as *category + distance + count*:

```
Rescue operation in progress    High       0 m away · 1 report
Fire reported                   Moderate   300 m away · 1 report
Building damage reported        Moderate   360 m away · 2 reports
```

No description, no reporter, no exact position — you learn "flooding reported near here" without
learning anything about the people who reported it. Flagged (unverified) reports are excluded
entirely, so an unconfirmed report cannot become a public warning.

There is no shelter or hospital dataset in the backend, so none is invented. The Services filter
shows the real responder roster instead.

---

## Control Room

Separate page, separate navigation, its own sign-in. Nothing from it appears in citizen navigation.

**Dashboard · My incidents · Queue · Review · Dispatch · Responders · Bottlenecks · Process log · Governance**

### Roles

Control Officer, Supervisor, Administrator. An officer's **My incidents** is derived from the process
log — the cases that operator has actually acted on — which is the only definition the backend can
support, since it assigns incidents to *responders*, not operators.

> **This is not authentication.** The backend has no auth layer, so role scoping is a UI affordance,
> not a security boundary. The sign-in screen states this in as many words. What *is* real is the
> attribution: every override and review lands in the append-only process log under that operator's
> name, which is what makes "my incidents" derivable at all.

### AI transparency

The case drawer separates the model's opinion from the human's:

```
AI ASSESSMENT
  94 SEVERITY    70 AUTHENTICITY    local SCORED BY
  ✓ Life risk: not breathing   ✓ Life risk: trapped
  ✓ 6–20 people affected       ✓ Vulnerable people: children

  AI RECOMMENDS: CRITICAL   |   HUMAN DECISION: NOT REQUIRED
```

None of this is visible to citizens.

---

## What the backend cannot do yet

Built honestly rather than mocked. Each of these needs backend work:

| Spec asks for | Status |
|---|---|
| Real authentication and enforced RBAC | **Not built.** No auth in the backend; sign-in is local and labelled as such. |
| Server-side audit of *reads* | Writes are logged (append-only); read access is not recorded. |
| Emergency profile / family stored server-side | On-device only, attached to report text by choice. |
| Cross-device tracking-code lookup | Codes resolve on the device that filed them. Needs an endpoint that maps a code to a report. |
| Machine translation of report content | Original text is sent intact. Signal chips bridge the gap; the AI layer will read all six languages natively. |
| Shelters, hospitals, relief centres | No dataset exists, so none is shown. |
| Bluetooth / Wi-Fi Direct mesh relay | Not built — and an explicit non-goal in PRD §3. Offline reports queue on-device and sync over IP. |

---

## Accessibility under stress

- Minimum 44 px tap targets; verified at 320 px wide with no horizontal overflow
- 16 px inputs, below which iOS zooms the page on focus
- Critical states always carry **text**, never colour alone
- Voice input to minimise typing
- `prefers-reduced-motion` disables transitions, the SOS ring pulse and the voice indicator
- Full-bleed with `100dvh` and `env(safe-area-inset-*)`; the desktop phone frame is presentation only

---

## Layout

```
frontend/
├─ index.html          Citizen PWA
├─ control.html        Control room
├─ css/                base · citizen · control
├─ js/
│  ├─ api.js           Typed client for all 16 endpoints
│  ├─ store.js         Pseudonymous identity + IndexedDB outbox
│  ├─ i18n.js          Six languages
│  ├─ profile.js       Emergency profile + family (on-device)
│  ├─ signals.js       Language-independent chips → normalised English
│  ├─ voice.js         SpeechRecognition, multilingual
│  ├─ geo.js           Location and Leaflet maps
│  ├─ ui.js            DOM helpers, formatting, reason labels
│  ├─ citizen.js
│  └─ control.js
├─ assets/leaflet/     Vendored — the venue's network is allowed to die
├─ manifest.webmanifest · sw.js · icons/
└─ serve.sh
```

Plain ES modules. No bundler, no framework, no `node_modules`.

---

## Offline

An IndexedDB outbox (not localStorage — one 2 MB photo base64s past its ~5 MB ceiling). On
reconnect it replays split by payload: text-only in one batch to `POST /api/sync/reports`, photos
individually through multipart. Both carry the same client idempotency key, so a report cannot be
created twice however often a sync retries, and a `duplicate` result counts as success.

`client_created_at` is stamped when the citizen writes the report, so a report filed offline keeps
the wait it accrued and does not lose its place in the queue.
