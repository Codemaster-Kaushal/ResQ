# RescueNet AI — Backend Product Requirements Document (PRD)

**Version:** 1.0
**Team:** CtrlWin — Lakshya Arora, Taashu Sharma, Kaushal Choudhary
**Event:** Hackverse 2.0, MIT Bengaluru
**Scope:** Backend services only. Frontend/PWA covered separately.

---

## 1. Purpose

Emergency dispatch systems process requests in arrival order. During a mass-casualty event
this means a sprained ankle reported at 09:00 is actioned before a building collapse reported
at 09:05.

The RescueNet backend replaces arrival-order dispatch with **severity-ordered, authenticity-verified
dispatch**, and records every state transition as a process event so response bottlenecks are
measurable rather than anecdotal.

---

## 2. Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | Rank incoming reports by life-risk, not arrival time | Severity-ordered queue differs from FIFO order on seeded dataset |
| G2 | Suppress false and duplicate reports before they consume responder time | Duplicate images and stale geo-time reports flagged automatically |
| G3 | Assign the best-fit responder, not the first free one | Assignment accounts for distance, skill, and current load |
| G4 | Make the response process observable | Every transition emitted as a process event; bottlenecks computed from the log |
| G5 | Accept reports filed with no connectivity | Batch sync endpoint reconciles queued offline reports without duplication |
| G6 | Never lose a report to a system failure | AI outage degrades to rule-based scoring; ingestion never rejects a valid report |

---

## 3. Non-goals (explicitly excluded from this build)

- Bluetooth / Wi-Fi Direct mesh relay
- Geospatial cluster maps and zone polygon rendering
- Social media ingestion and scraping
- Video ingestion (images only)
- SMS/telephony fallback
- Multi-tenancy, organisation hierarchies, RBAC beyond a role field
- Kafka, OpenShift, Celonis EMS live connection, watsonx (cost)
- Post-disaster analytics, insurance/claims exports

These remain in the product roadmap and may be referenced in the pitch as **future work**.
They are not implemented and must not be presented as implemented.

---

## 4. Users

| Persona | Needs from backend |
|---|---|
| **Citizen reporter** | Submit a report with text, one image, GPS. Accepted even when offline. Gets a tracking ID. |
| **Field responder** | Receives assignments. Updates status (acknowledged → en route → on scene → resolved). |
| **Control-room operator** | Reads the live priority queue, overrides any ranking, reviews flagged reports, sees bottlenecks. |
| **System** | Scores, ranks, assigns, and logs — without human intervention in the happy path. |

---

## 5. Functional requirements

### 5.1 Report ingestion
- **FR-1** Accept a report containing: free text, optional image, latitude/longitude, client timestamp, reporter pseudonym.
- **FR-2** Accept reports individually (online) or as a batch (offline sync).
- **FR-3** Assign every report a server ID and return it, plus a client-supplied idempotency key echo.
- **FR-4** Never reject a report for scoring failure. If scoring is unavailable, persist with status `received` and queue for retry.
- **FR-5** Store images with EXIF metadata preserved for the authenticity stage.

### 5.2 Triage and severity
- **FR-6** Classify incident type into a fixed taxonomy: `structural_collapse`, `flooding`, `medical`, `trapped_persons`, `fire`, `landslide`, `infrastructure`, `other`.
- **FR-7** Produce a severity score 0–100.
- **FR-8** Produce human-readable **reason codes** for every score. A score with no explanation is a failed requirement.
- **FR-9** Fuse text signals and image signals into one score. If the image is absent, degrade to text-only without failing.
- **FR-10** Complete triage within 5 seconds p95, or return the rule-based score and mark the record for AI backfill.

### 5.3 Authenticity and trust
- **FR-11** Produce an authenticity score 0–100 with reason codes.
- **FR-12** Detect duplicate or near-duplicate images via perceptual hashing.
- **FR-13** Check geo/time plausibility: client timestamp vs. server time, coordinate validity, impossible movement for a repeat reporter.
- **FR-14** Raise authenticity when independent nearby reports corroborate the same event within a time and distance window.
- **FR-15** Reports below the authenticity threshold are set to `flagged` and routed to human review. **They are never auto-deleted or auto-rejected.**

### 5.4 Priority queue
- **FR-16** Maintain an ordered queue of verified reports, ranked by a priority score derived from severity, authenticity confidence, and wait-time ageing.
- **FR-17** Ageing must guarantee no report is starved indefinitely.
- **FR-18** A control-room operator may manually pin, demote, or override any report's position; the override is recorded as a process event with the operator's identity.

### 5.5 Dispatch
- **FR-19** Match the top unassigned report to the best available responder using distance, skill match, and current load.
- **FR-20** Respect responder capacity. Never assign beyond capacity.
- **FR-21** Support assignment rejection by a responder, returning the report to the queue with its wait time preserved.
- **FR-22** Support the full status lifecycle through to `resolved` and `closed`.

### 5.6 Process intelligence
- **FR-23** Emit a process event for every state transition, with case ID, activity, actor, and timestamp.
- **FR-24** Compute per-transition cycle times and identify stages exceeding the bottleneck threshold.
- **FR-25** Export the full event log as CSV in process-mining-standard columns (`case_id`, `activity`, `timestamp`, `resource`).

### 5.7 Offline sync
- **FR-26** Accept a batch of offline-queued reports in one request.
- **FR-27** Deduplicate by client idempotency key. Re-submitting the same batch must be a no-op.
- **FR-28** Preserve the original client timestamp separately from server receipt time, and use the client time for wait-time calculations.

### 5.8 Governance
- **FR-29** Expose an endpoint returning model provenance, active scoring provider, thresholds in use, and the fallback state.
- **FR-30** Record every human override, so the human-in-the-loop claim is evidenced in data, not just in the pitch.

---

## 6. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Ingestion p95 latency under 300 ms excluding AI scoring (scoring is asynchronous). |
| NFR-2 | The API must remain functional with **zero** external network access. |
| NFR-3 | Zero paid services. Free tiers and local compute only. |
| NFR-4 | Seeded demo dataset must load in under 10 seconds and be reproducible. |
| NFR-5 | No unhandled exception may reach the client. All errors return typed, structured responses. |
| NFR-6 | Single-command local startup. Judges may ask to see it run. |
| NFR-7 | Reporter identity is pseudonymous. No name, phone, or email is required to file a report. |

---

## 7. Success criteria for the demo

1. A high-severity report filed **last** appears **first** in the queue, with visible reasons.
2. A duplicate image is flagged without human input.
3. A batch of offline reports syncs and is triaged; re-sending the batch creates no duplicates.
4. An assignment is produced and its full lifecycle is traceable in the event log.
5. The bottleneck endpoint returns a real finding computed from seeded event data.
6. All of the above works with the network disconnected.

---

## 8. Phase map

Ten phases, each independently demoable. Detailed acceptance criteria in the TRD.

| Phase | Outcome |
|---|---|
| 1 | Project skeleton, config, DB, health check |
| 2 | Data model, migrations, seed dataset |
| 3 | Report ingestion + media storage |
| 4 | Triage engine (classification + severity) |
| 5 | Authenticity and trust scoring |
| 6 | Priority queue with ageing and override |
| 7 | Dispatch and assignment engine |
| 8 | Responder status lifecycle |
| 9 | Process event log, cycle-time mining, CSV export |
| 10 | Offline batch sync, governance endpoint, hardening, deploy |

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Free-tier AI rate limits during demo | Local deterministic scorer is the default path; AI is an enhancement layer |
| Scoring latency stalls ingestion | Scoring runs async; ingestion returns immediately |
| Seed database wiped by a late migration | Seed is idempotent and re-runnable in one command |
| Scope creep back into mesh/maps | Non-goals in §3 are frozen for this build |
| Over-claiming in the pitch | Governance endpoint reports actual provider in use; team briefs honestly |
