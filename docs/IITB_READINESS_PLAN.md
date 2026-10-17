# OPD Saathi — IIT Bombay readiness: audit, gap analysis and phase plan

**Prepared:** 16 October 2026 (Day 1 of 15) · **Branch:** `feat/iitb-readiness` · **Data:** synthetic only

This document is the working plan for the state-level presentation at IIT Bombay. It records what the
repository actually does today (verified by reading code, running the suites and reproducing failures),
where it falls short of SIH26047, what we will change, in what order, and how each change is tested.
The progress checklist at the end is updated as work lands.

---

## 1. Summary

OPD Saathi is not a prototype that needs rebuilding. It is a mature FastAPI + PostgreSQL (row-level
security) + Redis + React/Vite PWA system with a deterministic interview engine, explainable triage,
encrypted clinical data, an audited staff console and 829 passing API tests. The work for the next 15
days is therefore **hardening and filling specific gaps**, not new architecture.

The audit found four defects that would break the live demonstration and several that a serious
technical evaluator would find quickly:

| # | Severity | Defect (verified) | Effect at the demo |
|---|---|---|---|
| D1 | **P0** | The web app calls `crypto.randomUUID()` in 11 places. Browsers only provide it in a *secure context* (HTTPS or `localhost`). Reproduced in Chromium with a Pixel 7 profile: from `http://172.27.14.36:5173` it is `undefined`. | On a phone or any second PC on the Wi-Fi, answering a question, confirming, and every staff queue action throws. Looks like "the network doesn't work". |
| D2 | **P0** | `PUBLIC_WEB_URL` (the address encoded in every token QR code) is baked into the API container when `start-demo.ps1` runs. Docker restarts containers with the old value. Running container: `172.17.134.95`; this PC now: `172.27.14.36`. Probing the old address times out. | Phones that scan the QR wait and fail — indistinguishable from a firewall block. |
| D3 | **P1** | `patients.phone_hmac` is unique per hospital and every kiosk session creates a new patient row. A returning patient, or a second family member using the same phone, gets `409 PHONE_ALREADY_REGISTERED`. No test covers this path. | Returning patients cannot give their number; the longitudinal-record feature cannot be built on this model. |
| D4 | **P0** | `no_show` is a terminal queue state. There is no "absent / skipped" state and no recall. End-of-day close-out also turns waiting patients into no-shows. | A patient who steps out when called loses the visit; the doctor-calling workflow requested for the demo is impossible. |
| D5 | P1 | Call, start, complete, no-show, cancel, call-next and override check the role but not the staff member's department. (Reads already check the department.) | A doctor in one department can act on another department's patient through the API. |
| D6 | P1 | `call-next` reads the queue outside any lock; two doctors pressing it together pick the same patient and the second gets an error. | Visible race at a two-doctor demo. |
| D7 | P1 | A doctor can hold several patients in `called`/`in_consult` at once; who called a patient is not recorded. | "Two active consultations" and no accountability for calls. |
| D8 | P1 | `POST /queue/{id}/routing/confirm` reads the case sheet column directly, but that column holds `{"ciphertext": …}`. The route is therefore never found and the call always returns `409 ROUTE_ALREADY_CONFIRMED`; if it ever proceeded it would overwrite ciphertext with plaintext. No test covers it. | The nurse "Confirm route" button never works. |
| D9 | P1 | If the database commits a confirmation but the final Redis write fails (or two confirms race), a retry gets `409 SUMMARY_NOT_READY` forever, although the patient is queued with a token. | A patient can be in the queue with no token on screen. |
| D10 | P2 | Answer corrections (`PATCH …/interview/answers`) are not idempotent; a retry after a lost response can hit the turn-number unique key. | Rare 500 on flaky Wi-Fi. |
| D11 | P2 | In development (`npm run dev`) the API base defaults to `http://localhost:8000`; on a phone that is the phone itself. | Dev-mode phone testing fails for a reason unrelated to the firewall. |
| D12 | P2 | Local test runs load `.env`, which holds live Gemini/Sarvam keys, so tests could call live providers. CI has no `.env` and is unaffected. | Cost and flaky local runs; synthetic data sent to a provider. |
| D13 | P2 | The offline outbox needs `crypto.subtle` (secure context only). | Must be hidden, not crash, on insecure origins. |

**What the "firewall issue" most likely was.** The host firewall is not blocking: Docker Desktop's
backend already has inbound *Allow* rules on the Public profile, there are no matching *Block* rules, and
the web container listens on `0.0.0.0:5173`. The failures a phone user would see are explained by D2
(QR points at a dead address), D1 (buttons throw once the page loads), and — at a venue — Wi-Fi client
isolation or a different subnet (this PC is on a wired `172.27.x` network; campus Wi-Fi commonly isolates
clients). Section 6.1 gives the operator procedure and a diagnostic script; we will not disable the firewall.

---

## 2. SIH26047 — verified problem statement

The official portal (`sih.gov.in/sih2026PS`) lists problem statements in descending order and only the
first ~100 (down to SIH26160) are present in the static page, so SIH26047 could not be fetched from it
automatically. The text below is taken from secondary copies that reproduce the official entry; two
independent copies agree on every field except the theme. **Before the presentation, a team member
should open the official portal in a browser, search 26047 and confirm the wording.**

- **Title:** Patient Case-Taking Software · **ID:** SIH26047
- **Organisation:** Ministry of Ayush · **Department:** All India Institute of Ayurveda (AIIA)
- **Category:** Software · **Theme:** MedTech / BioTech / HealthTech (one copy says "Smart Automation")
- **Problem:** OPD consultations last minutes, so histories are incomplete; AYUSH practice needs an even
  more extensive history (Prakriti, Vikriti, Agni). Patients bring fragmented paper records. ABDM exists,
  but there is no patient-facing "first-mile" tool to capture history and digitise documents *before* the visit.
- **Expected solution ("MediKiosk"):**
  - **A — Conversational history engine:** adaptive voice-and-touch interview in the patient's language,
    SOCRATES-style follow-ups, an AYUSH extended mode (Dashavidha Pariksha), red-flag detection that
    raises priority triage alerts.
  - **B — Document digitisation:** multilingual OCR of printed and handwritten documents; extraction of
    diagnoses, medicines, lab values and procedures; a chronological timeline; flagging of abnormal values
    and potential drug interactions.
  - **C — Summary generator:** standard sections (CC, HPI, past, drug and allergy, family, personal, ROS,
    prior investigations) that the physician can edit, accept or reject; a draft, never a diagnosis;
    patient audio in the local language, physician output in English or Hindi.
  - **D — Consent, privacy and ABDM:** ABHA-based authentication, granular revocable consent with audio
    explanation, session data cleared after submission, FHIR push to HIS/EMR and the ABHA PHR, DPDP Act 2023.
  - **Journey:** identify and consent → converse → scan documents → summarise and route → physician reviews.

Sources: [SIH 2026 problem statements](https://sih.gov.in/sih2026PS) (official, not reachable for this ID),
[SIH explorer copy](https://sih-explorer.amanuniyal47.workers.dev/problem/SIH26047),
[repository quoting the official metadata](https://github.com/xarjunpatil/SIH26047-Patient-Case-Taking-Software).

### 2.1 Requirements matrix

Priority: **P0** demo-breaking · **P1** needed for a credible demo · **P2** strengthens the demo · **P3** future.

| # | SIH26047 requirement | Why it matters | Current implementation (verified) | Gap | Proposed improvement | Priority | Effort / dependencies | Test and demonstration |
|---|---|---|---|---|---|---|---|---|
| R1 | Multilingual interaction | Patients describe problems in their own language | Full UI, question bank, consent and read-back in English, Hindi, Marathi (407 keys/language, parity-checked) | Other regional languages; translations not native-reviewed | Keep three languages for the demo; document the add-a-language path (bank + locale files) | P2 | Native-speaker review is a people task | Locale parity script; e2e language switch mid-interview |
| R2 | Voice **and** touch history-taking | Low-literacy and elderly users | Sarvam/Gemini STT with browser fallback; TTS read-aloud; tap and type always available | Microphone is unavailable on phones over plain HTTP (no secure context); the UI doesn't say why | Explain "voice needs the secure link" and fall back to tap/type; optional HTTPS-on-LAN using the existing nginx | P1 | nginx TLS config + local CA script; no new component | Secure-context probe; phone e2e with mic unavailable |
| R3 | Adaptive follow-up questions | Complete, relevant history in few questions | Rule-triggered modules from the chief concern; ≤3 AI follow-ups with bank fallback; Back/Change | No surgical or personal history; "don't know/declined" not distinguished from "no" | Add surgical and personal-history questions; record *unknown* and *declined* explicitly | P2 | Bank JSON + engine; regenerate golden set and audio manifest | Interview golden set; unit tests for unknown ≠ negative |
| R4 | Structured history (CC, HPI, PMH/PSH, meds, allergies, FH, personal, ROS) | Physician-ready summary | Versioned `CaseSheet` schema covers CC, HPI (SOCRATES fields), past history, medicines, allergies, family, social, red flags, routing, per-field provenance | ROS absent; surgeries/social never asked | As R3; a short ROS screen is deferred | P2 | Schema already has fields | Case-sheet schema check in CI |
| R5 | AYUSH history (Trividha, Ashtavidha, Dashavidha Pariksha) | The problem owner is AIIA | None | No AYUSH parameters at all | Optional Ayurveda module: patient-reportable items (Ahara Shakti/appetite, Mala/bowel, Mutra/urine, Nidra/sleep, Vyayama Shakti, diet/Satmya) asked by the kiosk; examination items (Nadi, Jihva, Drik, Akriti, Prakriti…) as clinician-entry fields, never asked of the patient. All marked draft pending vaidya review | P2 (high demo value) | Bank module + case-sheet section + consultation UI | Unit tests; demo with AYUSH mode on |
| R6 | Document scanning, OCR, extraction, chronology | Fragmented paper records | Prescription photo → Gemini reads medicine names; image never stored. `documents` table exists but is unused | No storage, no lab-value extraction, no timeline, no review of uncertain results | Upload/capture with type and size validation, encrypted local storage, staff preview, AI extraction stored as *unconfirmed* until a clinician accepts it; abnormal flags only with a stated reference range | P3 (P2 if time) | Storage adapter, migration, UI | Integration tests for invalid/oversized files and extraction failure |
| R7 | Physician-readable, editable AI summary | Saves consultation time; must stay a draft | Case sheet + AI brief + doctor edits with optimistic concurrency + PDF; strong output guard rejects diagnosis/advice wording | No accept/reject state for the AI draft; no link from summary statements to the answers that support them | Review state (draft → accepted/edited/rejected, by whom, when); `brief_sources` limited to asked question ids and shown as links | P2 | Case-sheet schema 1.0 additive fields | Unit tests for provenance validation; e2e accept/reject |
| R8 | Red-flag detection and escalation | Patient safety | Deterministic rules + danger tiles raise an immediate staff alert, re-escalated every 60 s until acknowledged; AI second opinion counts as *priority* at most | A self-reported urgent flag jumps the queue with no "awaiting confirmation" marker; queue gaming via danger tiles is possible | Keep urgent at the front (never suppress), but mark self-reported urgent as *awaiting nurse confirmation*; nurse confirms or lowers with a recorded reason | P1 | Queue phase | Adversarial unit tests; demo red-flag scenario |
| R9 | Consent, privacy, access control, secure handling | DPDP Act 2023; trust | Versioned consent per visit (text hash), AES-GCM encrypted PII/case sheets/transcripts, RLS tenant isolation, role checks, append-only audit, retention script | Single blanket consent; no withdrawal; department authorisation gaps (D5); D8 writes plaintext | Department checks everywhere; separate consent purposes (care, AI processing, record linking); withdrawal path; fix D8 | P1 / P2 | Migration for consent purposes | Authorisation and tenant-isolation tests |
| R10 | HIS / ABDM / FHIR integration | Interoperability | None (documented as out of scope) | — | FHIR R4 *export* of one encounter (Patient, Encounter, Condition, AllergyIntolerance, MedicationStatement, Observation, QuestionnaireResponse) labelled "not ABDM-certified"; ABHA/ABDM documented as future | P3 | Mapping module | Validate bundle structure in unit tests |
| R11 | Returning patients / longitudinal record (brief, Feature 2) | Patients shouldn't repeat their history | Each visit creates a new patient row; no linking; D3 | Everything | OPD card number + card code (QR on the slip) to link a new visit to an existing patient; nurse-assisted search and link; doctor's previous-visits panel; never auto-merge on phone/name | P1 | Migration (drop unique phone index), APIs, UI | Integration tests incl. shared phone, uncertain match, cross-hospital |
| R12 | Session data cleared after submission | Module D | Redis session TTL 2 h; kiosk clears browser storage on finish | Redis state kept for 2 h after confirmation | Delete interview state after confirmation, keep only the receipt needed for idempotent retries | P2 | Small | Unit test |

---

## 3. Repository audit

### 3.1 Architecture (as built)

```
Phone / kiosk browser ──HTTP──► nginx (web container, :5173) ──/api──► FastAPI (gunicorn, 2 workers)
                                   │ static PWA (React/Vite)            │
                                   │                                     ├─► PostgreSQL 16 (RLS by hospital)
                                   │                                     ├─► Redis (durable: sessions, queue read model, pub/sub, idempotency)
                                   │                                     ├─► Redis cache (evictable)
                                   │                                     └─► Gemini / Sarvam (optional, explicit keys)
Worker (ARQ): outbox relay every 2 s → Redis sorted sets + status pub/sub; notifications; analytics refresh; day close-out
```

Key modules: `services/api/app/api/v1/intake.py` (session, consent, identity, danger signs, interview,
confirm), `queue.py` (status page, staff queue, transitions, alerts, WebSocket), `case_sheets.py`
(doctor read/edit/PDF/transcript), `db/repositories.py` (all SQL), `domain/interview` (bank + engine),
`domain/triage` (explainable score), `domain/redflags` (rules), `domain/queue` (state machine, scoring,
tokens, Redis read model), `workers/main.py`. Web: `KioskIntake.tsx`, `StaffQueueBoard.tsx`,
`DoctorConsultation.tsx`, `PatientStatus.tsx`, `lib/intake.ts`.

### 3.2 Journeys traced

- **Patient (kiosk):** `/kiosk` (activated kiosk, device secret in localStorage) → `POST /intake/sessions`
  creates a *patient row and a visit* (`registering`) and returns a 2-hour patient JWT; state lives in
  Redis `osa:intake:{hospital}:{visit}` → consent (versioned text hash) → identity (age, sex, optional
  phone) → danger tiles (instant urgent alert) → one question at a time; every answer is a durable,
  encrypted `interview_turns` row and re-runs red-flag rules and the triage score (monotonic: never falls
  on its own) → summary read-back → `POST /confirm` assigns the daily token under an advisory lock and a
  `FOR UPDATE` row lock, writes a `queue.changed` outbox event, enqueues in Redis → token, position,
  department and QR to `/s/<status-token>` (HMAC-derived, stored only as a hash).
- **Patient (phone):** today only the status page `/s/<token>` (SSE stream). There is no way to start
  an interview on a phone.
- **Nurse:** `/staff` sign-in (hospital picker, 10-minute access token, per-account refresh cookie) →
  Queue board (WebSocket snapshot + deltas, alert feed, override with reason, call/no-show, assisted
  registration).
- **Doctor:** Queue → open case sheet (department-checked, audited read) → edit with `If-Match` →
  start/finish consult → PDF.
- **Admin:** Reports (rollup + CSV), Settings (departments, staff, kiosks with one-time activation codes,
  AI switch, report settings), audit API.

### 3.3 Strengths to preserve

Tenant isolation by RLS with composite tenant foreign keys; field-level AES-GCM with associated data;
deterministic safety rules authoritative over AI; idempotency store for staff POSTs; outbox + rebuildable
Redis read model; explainable triage with every point listed for staff; extensive eval sets; 829 API tests.

### 3.4 Baseline (16 Oct 2026, before any change)

| Suite | Result | Notes |
|---|---|---|
| API (pytest, unit + integration) | **829 passed, 1 skipped** | Run against an isolated `osa_test` database and Redis DB 5 with AI keys blanked (see 11.4) |
| Web unit (Vitest, Node 24) | **55 passed** (13 files) | |
| E2E (Playwright) | to run before each commit | Specs mock the API with `page.route` |
| Secure-context probe | localhost ✓, LAN ✗ (`randomUUID`, `subtle`, `mediaDevices`, service worker all missing) | Reproduces D1 |
| LAN reachability | current IP `/api/healthz` 200; QR IP times out | Reproduces D2 |

---

## 4. Target design

### 4.1 Mobile access on the hospital LAN (Feature 1)

1. **Secure-context-safe client** (fixes D1, D13): one `newRequestId()` helper that uses
   `crypto.randomUUID` when present and otherwise builds an RFC 4122 v4 id from `crypto.getRandomValues`
   (available on every origin). Offline intake is offered only where `crypto.subtle` exists.
2. **Phone check-in points** (new): an administrator creates a *phone check-in QR* (stored as a kiosk
   record of kind `mobile` with its own code hash; it can never obtain a kiosk device token). Patients
   scan it, open `/m/<code>`, choose a language and complete the **same** server-side interview the kiosk
   uses. Session creation is rate limited per client address and per check-in point; visits are marked
   as phone check-ins on the staff board so staff can greet and verify them.
3. **No duplicates on refresh or reconnect:** the phone keeps its session in `localStorage` (the kiosk
   keeps `sessionStorage`), resumes at the current step after a reload, and after confirmation remembers
   the issued token so reopening the check-in link shows "You already have token MED-012" instead of
   starting another visit. Confirmation becomes idempotent on the database (fixes D9).
4. **Correct QR address and staff-visible reachability** (fixes D2): `scripts/lan-check.ps1` reports the
   LAN address, network profile, bindings, firewall rules and whether the running API's phone address
   matches this PC; `start-demo.ps1` warns when it doesn't. Staff Settings shows the phone address and a
   live reachability test.
5. **Voice on phones (optional, P2):** HTTPS on the LAN using the nginx we already ship and a locally
   generated CA (no new component, per ADR 001). Without it, phones use tap and typing, and the UI says so.

### 4.2 Patient identity and longitudinal record (Feature 2)

- **Identity ≠ encounter.** `patients` stays the per-hospital person; `visits` is the encounter with its
  own token, consent, interview and consultation.
- **Matching never trusts demographics.** The unique phone index is replaced by a non-unique index
  (shared family phones). A phone match is only a *suggestion* shown to staff.
- **Linking a returning patient** requires something only that patient holds: the **OPD card** — a
  hospital registration number (`26-000123`, assigned when the first visit is confirmed) plus a 6-digit
  PIN, both printed on the token slip and shown on the phone. The PIN is derived with the HMAC key from
  the patient id and a card version, so it is never stored and can be re-printed. Entering both at a
  kiosk or phone links the new visit to the existing patient, provided today's age and sex fit the record
  (birth year ±1; female/male must agree). Attempts are rate-limited (5 per visit per 15 min, 10 per
  number per hour) and every outcome is audited. *Changed from the first draft:* a typed number + PIN
  instead of a QR card code, because the kiosk then needs no camera and the code needs no stored hash.
- **Without the card**, a nurse searches by OPD number or phone (results show number, age, sex, visit
  count and last visit — no names) and links after checking identity in person; the search and the link
  are audited. A patient who registered as new because they forgot the card can be joined later; their
  one-visit record is retired. No automatic merges: linking is refused when both records have history.
- **What each side sees.** The shared kiosk never displays past clinical details (privacy on a public
  screen); after a card link it shows only the date of the last visit and asks whether today is a
  follow-up. The doctor sees a *Previous visits* tab — date, department, token, chief complaint, red
  flags, the AI draft brief (marked when staff edited the case sheet), and allergies/medicines/conditions
  reported at earlier visits with the date last reported — separated from today's interview and
  labelled as patient-reported. Reads are authorised server-side only through a visit in the staff
  member's department that is open or from today (admins always), and audited.
- **Follow-up encounters** reference the earlier visit (`previous_visit_id`) without modifying it.

### 4.3 Queue: state machine, fair priority, abuse resistance (Features 3 and 4)

```
registering ──► awaiting_confirmation ──► queued ──► called ──► in_consult ──► done
     │                    │                 ▲  │        │             │
     └──► cancelled ◄─────┴─────────────────┼──┴────────┤             └──► cancelled
                                            │           ▼
                                      recall│       skipped (temporarily absent) ──► no_show
                                            └───────────┘
```

- **New state `skipped`** (called, did not appear). The doctor marks the patient absent after a
  configurable grace period (default 3 minutes; the timer prompts, it never cancels by itself) and calls
  the next eligible patient. **Recall** returns the patient to `queued` with the *same* token, encounter,
  urgency and original arrival time, so they are seen next within their priority class. `no_show` is
  reached only from `skipped` (manually, or at end of day). *Added while building:* **put back**
  (`called → queued`) for a call made by mistake, without a missed call on record; and the end-of-day
  close-out now records still-waiting patients as `cancelled` (not seen before closing), not `no_show`.
  The full rules, permissions and open decisions are in `docs/QUEUE_POLICY.md`.
- **One active claim per doctor:** a doctor cannot call or start a patient while already holding one in
  `called`/`in_consult`; `called_by` is recorded. **`call-next` is serialised per department** with a
  transaction-scoped advisory lock, so two doctors never receive the same patient.
- **Department authorisation** on every transition (fixes D5); admins may act hospital-wide.
- **Audit and history:** each transition writes an immutable `visit_events` row (from, to, actor,
  reason) besides the audit log; the doctor's consultation page shows this timeline, the patient's
  status page shows the current state (including "We missed you, go to the desk"), and the original
  encounter is never lost.
- **Priority policy (documented in `docs/QUEUE_POLICY.md`):** urgent first (always, never capped);
  within routine/priority, virtual arrival time = arrival − head start (bounded by level; at most
  45 minutes); ties by arrival then id (deterministic). Self-reported urgent flags stay at the front but
  are marked *awaiting nurse confirmation*; the nurse confirms or lowers with a reason. The server alone
  computes urgency and position; answers cannot be changed after confirmation; urgency never falls
  without a recorded staff decision. We do **not** penalise inconsistent answers — we route them to a human.

### 4.4 AI summary and documents (Feature 5)

Review state for the AI draft, statement-to-answer provenance, failure handling already present
(timeouts, schema validation, output guard, deterministic fallback) kept. Documents follow in Phase 4
if time allows: upload + preview + metadata + review first, OCR extraction stored as unconfirmed.

*As built (Phase 4):* the model returns the brief as sentences, each citing the `question_id`s of the
answers that support it. The server keeps only citations to questions this patient was asked and
drops any sentence left without one, so every statement can be checked. The output guard checks
each sentence. The doctor sees source chips that open the answer in the transcript, and records
**accepted**, **edited** (their own words; the AI wording is kept) or **rejected** (`POST
/visits/{id}/ai-summary/review`, doctor of the treating department, `If-Match` version, audited).
The PDF prints who reviewed it and never prints a rejected draft. The background AI step never
overwrites a reviewed summary. Documents are deferred to Phase 6 (see the checklist).

*As built (Phase 6):* on the review screen the patient taps what a paper is (prescription, test
report, discharge paper, other) and photographs it; up to five, removable until confirmation. Staff
can photograph more while the patient is under care (ten per visit in all). Photos are checked
(JPEG/PNG/WebP whose bytes match the type, at most 1 MB) and stored encrypted, bound to their
document. With the patient's AI consent, Gemini reads each paper in the background into an
unconfirmed draft (diagnoses, medicines, results with the printed range, procedures, the printed
date). The doctor's *Old papers* tab shows each photo beside its draft and records **matches the
photo** or **does not match**. Results are flagged above/below/within only against the range printed
on the same paper, by arithmetic in `domain/documents.py`; nothing is flagged from a general range or
from the model. Earlier visits' papers follow the earlier-visits rule. Without AI consent the photo
is kept unread.

### 4.5 Future RAG assistant (Feature 6) — design only

Documented in `docs/RAG_DESIGN.md`: retrieval scoped by patient id and hospital *before* search,
citation of record ids for every factual answer, refusal when unsupported, retrieved text treated as
untrusted data, no medication advice, urgent-symptom routing, evaluation set. No implementation now.

---

## 5. Database and API changes

All changes are additive Alembic migrations with working downgrades; nothing is dropped that holds data.

| Migration | Change | Why |
|---|---|---|
| 0017 `mobile_checkin` | `kiosks.kind` (`device`/`mobile`), `kiosks.checkin_code_hash` (unique, own RLS lookup policy), constraints that a mobile point never has device secrets; `consents.channel` allows `mobile` | Phone check-in points |
| 0018 `patient_identity` (done) | Drop unique `uq_patients_hospital_phone_hmac`, add a non-unique index; `patients.mrn` (unique per hospital) and `patients.card_version`; `patient_number_counters` (per hospital and year, RLS); `visits.patient_link` (`card`/`staff`) and `visits.previous_visit_id` (tenant FK) | Returning patients, shared phones (D3) |
| 0020 `interview_settings` (done) | `hospital_interview_settings.ayurveda_module` (RLS, audited changes) | Ayurveda history per hospital |
| 0019 `queue_recall` (done) | `visits.status` allows `skipped`; `visits.called_by`, `skipped_at`, `recalled_at`, `skip_count`; `visit_events` table (RLS, insert/select only for the app role, insert for the worker); `hospital_queue_settings.call_grace_seconds` | Missed-patient workflow, audit history |
| 0021 `consent_purposes` (done) | `consents.purpose` (`care`/`ai`, one of each per visit), `withdrawn_at`, `withdrawn_by` (`patient`/`staff`) | Optional, revocable AI consent (R9) |
| 0022 `documents` (done) | `documents.kind`, `source` (`patient`/`staff`, with `uploaded_by`), `document_date`, `review_status`/`reviewed_by`/`reviewed_at`; `ocr_status` gains `skipped`; `document_contents` (the encrypted photo, RLS, cascades with its document); the app role may delete a document (a patient's own, before confirming) | Old papers (R6) |

New/changed endpoints (all server-authorised). Built: `GET /intake/checkin/{code}` and
`POST /intake/checkin/{code}/sessions` (public, rate-limited), `GET`/`POST /admin/kiosks/{id}/checkin-link`,
`POST /intake/sessions/{id}/opd-card`, `POST /intake/sessions/{id}/follow-up`,
`GET /visits/{id}/history` (staff, department and open-visit rule), `GET /patients/lookup` (staff),
`POST /queue/{id}/link-patient` (nurse/admin), `POST /queue/{id}/skip`, `POST /queue/{id}/recall`,
`POST /queue/{id}/return` (put back after a mistaken call), `POST /queue/call-next` (now serialised
and idempotent), `GET /visits/{id}/queue-events`, `GET`/`PATCH /admin/queue-settings`,
`POST /intake/sessions/{id}/consent/ai`, `POST /status/{token}/ai-consent/withdraw` (public, can only
stop), `POST /visits/{id}/ai-consent/withdraw` (staff), `GET`/`POST /intake/sessions/{id}/documents`
and `DELETE …/documents/{document_id}` (the patient, while checking in), `GET`/`POST
/visits/{id}/documents`, `GET …/documents/{document_id}/content` and `POST …/{document_id}/review`
(staff of the department; review by doctors only). Dropped:
`GET /system/phone-access` — `scripts/lan-check.ps1` covers it from the host, where the firewall
and network profile can actually be read.

---

## 6. Operations

### 6.1 Phone access on the hospital LAN — operator procedure (summary; full guide in `docs/MOBILE_LAN.md`)

1. Connect the server PC and the phones to the **same** Wi-Fi/LAN segment. At venues with client
   isolation (most campus and guest Wi-Fi), use a dedicated router or a phone hotspot that the server PC
   also joins.
2. Run `.\start-demo.ps1` (again after every reboot or network change — it recomputes the phone address).
3. Run `.\scripts\lan-check.ps1`. It must report the phone address as reachable and matching the running API.
4. If Windows marks the network *Public* and phones still cannot connect, prefer switching the trusted
   hospital network to *Private*. Only if required, add the narrowly scoped rule the script prints
   (TCP 5173, local subnet only) and remove it afterwards with the printed command. Never disable the firewall.
5. Print the phone check-in QR from Staff → Settings → Phone check-in.

### 6.2 What works without the internet

| Capability | No internet, LAN up | Server PC down | Fully disconnected phone |
|---|---|---|---|
| Kiosk and phone interview (tap/type), tokens, live queue, staff console, case sheets, PDF (Docker) | ✓ | ✗ | ✗ |
| Rules-based red flags, routing, triage score | ✓ | ✗ | ✗ |
| Browser speech recognition (Chrome) | ✗ (uses Google servers) | ✗ | ✗ |
| Sarvam/Gemini speech, AI summary, prescription reading | ✗ (degrades to tap/type and the deterministic case sheet) | ✗ | ✗ |
| Kiosk offline short form (encrypted outbox, replay on reconnect) | n/a | ✓ on the kiosk PC only | ✗ |

---

## 7. Security and clinical-safety considerations

- **Authorisation is server-side only:** department checks on every queue transition and history read;
  RLS remains the tenant boundary; the client never sends urgency or position.
- **Phone check-in abuse:** a check-in code is semi-public (printed in the hospital). Mitigations: rate
  limits per address and per point, codes rotatable by admins, visits marked as phone check-ins, sessions
  enter the queue only after a complete interview and confirmation, urgent flags always go to a human.
- **Card codes** are 128-bit secrets stored only as HMAC digests; the shared kiosk never shows history.
- **No PII in logs or URLs:** status and card tokens are opaque; existing structured logging already
  omits payloads; new code follows the same pattern.
- **Clinical safety:** no change weakens red-flag escalation; urgency is monotonic unless staff record a
  reason; AI output remains a labelled draft that cannot raise urgency above *priority* on its own; all
  rules, weights, questions and AYUSH items stay marked `draft_needs_clinician_review`.
- **Legal:** a consent screen is not DPDP compliance. We record purpose, version, timestamp and channel,
  and document what a hospital deployment would still need (DPO, notice in all languages, retention
  schedule, breach process, processor agreements with AI providers).

*As built (Phase 5):* every route that names a record by id is probed as another hospital, another
department and another patient's session, and must answer "not found" without changing anything
(`test_access_isolation.py`; an inventory check fails when a new record route has no probe). The
probes and the fifteen scenarios found eight defects, all fixed and listed in
`docs/E2E_SCENARIOS.md`. Most serious: a patient or kiosk session could cancel a visit that already
had a token, and a correction sent after a lost confirmation reply could reopen the interview and
drop a queued patient back to `registering`. Patient sessions now change a visit only until its
token is issued, checked against the database rather than the session. A staff board's live
connection closes when its sign-in expires or the account is deactivated. Session data is cleared
after submission (R12): the Redis session then keeps only the visit, kiosk, channel and language.

---

## 8. Test plan and acceptance criteria

| Area | Tests (synthetic data only) | Acceptance |
|---|---|---|
| Secure context | Vitest for the id helper with and without `randomUUID`; Playwright phone profile on the LAN origin | Interview and staff actions succeed from `http://<LAN-IP>:5173` |
| Phone check-in | API unit + integration: valid/invalid/turned-off code, rate limit, resume, no duplicate visit on retry | A phone completes the interview end-to-end and sees its live status |
| Confirmation | Concurrent and repeated confirms return the same token | Exactly one token per visit |
| Identity | Shared phone no longer blocked; card link; wrong code; cross-hospital code; nurse link audited | No automatic merges; history visible only to authorised staff |
| Queue | State-machine unit tests (every allowed/forbidden transition); skip → recall keeps token and arrival; one active claim per doctor; concurrent call-next distinct; department checks | Missed patient recovered with the original encounter and audit trail |
| Priority | Existing monotonicity/adversarial evals + new tests: tapped-everything cannot reach urgent; self-reported urgent marked for confirmation; override persists | Documented policy matches tests |
| Routing confirm | Decrypt/encrypt round trip | Button works; column stays ciphertext |
| Regression | Full pytest, Vitest, Playwright, ruff, mypy --strict, eslint, tsc, build, locale parity, OpenAPI types | All green before each commit |

---

## 9. 15-day schedule (Day 1 = Thu 16 Oct 2026)

| Days | Phase | Content | Exit criterion |
|---|---|---|---|
| 1 | 0 Audit | This document, baseline, reproductions | ✓ |
| 1–3 | 1 Reliability + mobile | D1, D2, D8, D9, D11–D13; phone check-in points; LAN guide and script; phone layout pass | Phone completes interview on LAN; all suites green |
| 4–6 | 2 Identity + records | Migration 0018, OPD card, link flows, doctor previous-visits panel, D3 | Returning-patient demo works; authorisation tests pass |
| 7–8 | 3 Queue | Migration 0019, skip/recall, claims, call-next lock, D5–D7, policy doc | Missed-patient demo works; concurrency tests pass |
| 9–10 | 4 Clinical | AI review state + provenance, AYUSH module, surgical/personal history; documents if time | Doctor accepts/edits a sourced draft |
| 11–12 | 5 Security + E2E | Tenant/department isolation tests, the 15 scenarios in section 10, load smoke | Scenario checklist complete |
| 13–14 | 6 Polish + demo | UI polish (patient, token, queue, consultation), demo data, rehearsal script | Two clean rehearsals from a cold start |
| 15 | 7 Freeze | Final regression, LAN check on real phones, limitations doc | Release tag on the branch |

## 10. End-to-end scenarios (tracked in the checklist)

1 first-time patient on a phone · 2 returning patient with a previous visit · 3 incomplete interview ·
4 red-flag symptom · 5 duplicate submission on a slow network · 6 called patient does not appear ·
7 skip and recall · 8 patient returns after being marked absent · 9 AI provider unavailable ·
10 network interruption · 11 two doctors in conflict · 12 another patient's records ·
13 another hospital's records · 14 invalid/oversized document · 15 simultaneous registrations.

What each must do, the tests that show it, and the defects they found: `docs/E2E_SCENARIOS.md`.

## 11. Deferred (with reasons)

- ABHA authentication and ABDM consent-manager integration — needs approved sandbox credentials and
  certification; we provide a FHIR export and documented mapping only.
- Handwriting OCR in Indian languages — accuracy cannot be validated in 15 days; upload/review flow first.
- Patient self-service access to past records — needs stronger patient authentication than a printed
  card (OTP/ABHA); the doctor-side history is built now.
- Drug-interaction checks and abnormal-lab flags against general reference ranges — need a licensed,
  reviewed knowledge base and reference ranges; never shipped as an unreviewed AI judgement. (Phase 6
  compares a result only with the range printed beside it on the same paper.)
- A chronological timeline across papers — the papers are listed newest first by the date printed
  on them; a merged timeline of diagnoses and results needs reviewed readings first.
- RAG assistant — design document only.
- Additional regional languages — after native-speaker review of the current three.

### 11.4 How tests are run locally

`.env` contains live provider keys, so local suites run with the keys blanked and against an isolated
database: create `osa_test` with the same roles (see `services/api/scripts/init-db.sh`), migrate it, and
export `DATABASE_URL`/`MIGRATIONS_DATABASE_URL`/`WORKER_DATABASE_URL` for `osa_test`,
`REDIS_STATE_URL=…/5`, `GEMINI_API_KEY=` and `SARVAM_API_KEY=` before `pytest`. Never run the suite
against the demo database (failed tests leave synthetic hospitals in the sign-in picker).

---

## 12. Progress checklist

| Phase / task | Status | Files | Tests | Notes |
|---|---|---|---|---|
| 0 Audit, SIH research, baseline, reproductions | done | this file, `PROBLEM_STATEMENT.md` | 829 API / 55 web baseline | D1–D13 recorded |
| 1.1 D1 secure-context-safe request ids | done | `lib/ids.ts` + 5 call-site files | `ids.test.ts`; e2e phone flow with `randomUUID` removed | Every write now works over plain-HTTP LAN pages |
| 1.2 D13 offline form only where Web Crypto exists | done | `offlineOutbox.ts`, `KioskWelcome.tsx` | e2e regression | |
| 1.3 D8 route confirmation decrypts via repository | done | `queue.py` | `test_route_confirmation_…` (failed before the fix with 409), unit test rewritten to the encrypted reality | Column stays ciphertext |
| 1.4 D9 one token per visit on retries and double taps | done | `intake.py` (`_confirmed_receipt`, `_queue_receipt`) | 2 integration tests (failed before: 409 / `[200, 409]`) | Red-flag inserts made conflict-safe for the same race |
| 1.5 D12 tests never call live providers | done | `tests/conftest.py` | full suite | |
| 1.6 D11 dev server proxies `/api` like nginx | done | `vite.config.ts`, `api.ts`, `playwright.config.ts` | 5 e2e matchers updated to accept `/api` | Local e2e can no longer reach a running API |
| 1.7 Phone check-in points (migration 0017) | done | `0017_mobile_checkin.py`, `domain/checkin.py`, `intake.py`, `admin.py`, `auth.py`, `repositories.py`, `cli.py`, `MobileCheckin.tsx`, `KioskShell.tsx`, `KioskIntake.tsx`, `AdminConsole.tsx`, `StaffQueueBoard.tsx`, locales | 4 integration + 2 unit + 3 e2e (phone viewport) | Rate limits 6/phone/QR and 150/QR per 10 min; Phone badge for staff |
| 1.8 D2 stale QR address: diagnosis and operator tooling | done | `scripts/lan-check.ps1`, `start-demo.ps1`, `MOBILE_LAN.md`, `start.md` | script run on this PC: reports the 172.17.134.95 vs 172.27.14.36 mismatch | Fix for the running stack: run `start-demo.ps1 -NoBuild` |
| Phase 1 gate | green | — | API 838 passed / 1 skipped; web 57; e2e 135; ruff, mypy, eslint, tsc, locales, contrast, build, bandit, semgrep (python, security-audit, sql-injection packs), migrations base→head | Entry bundle 566.8 kB vs 547.3 kB on `main` (+19.5 kB, mostly new hi/mr strings; all locales ship in the entry, a lazy-loading follow-up) |
| 2.1 D3 shared phones no longer block check-in | done | `0018_patient_identity.py`, `intake.py`, `KioskIntake.tsx` | `test_a_shared_phone_number_no_longer_blocks_a_second_patient` | The phone-conflict screen is gone; a phone number never links records by itself |
| 2.2 OPD number and card PIN on the token slip | done | `0018`, `domain/patients.py`, `repositories.py` (`assign_mrn`), `intake.py` (`_opd_card`), `KioskIntake.tsx` | unit (`test_patient_identity.py`, 10 cases) + integration | Number assigned on first confirmation; PIN derived, never stored; the returning visit's slip shows the same card |
| 2.3 Patient links today's visit with the card | done | `intake.py` (`/opd-card`, `/follow-up`), `KioskIntake.tsx` (new "returning" step), locales | integration: link + follow-up; refusals (wrong PIN, unknown number, age/sex mismatch, 6th attempt → 429, cross-hospital); 3 web unit; e2e flows updated | A separate step after age/sex, because the card fields overflowed the identity screen at kiosk size |
| 2.4 Nurse finds and links a patient without the card | done | `queue.py` (`/patients/lookup`, `/queue/{id}/link-patient`), `PreviousVisits.tsx` | 2 integration (desk link; joining a patient who registered as new, merge refused) | Search shows no names or clinical details; both actions audited |
| 2.5 Doctor's *Previous visits* tab | done | `case_sheets.py` (`/visits/{id}/history`), `PreviousVisits.tsx`, `DoctorConsultation.tsx`, `screens.css` | integration: department, hospital and closed-visit isolation; 2 web unit | Reads audited; refused (`HISTORY_CLOSED`) once the visit is closed and in the past; admins exempt |
| 2.6 Privacy notes | done | `PRIVACY.md` | — | Data map rows for OPD number, PIN, visit links, phone storage; linking and history rules |
| Phase 2 gate | green | — | API 854 passed / 1 skipped; web 61; e2e 135 of 136 in the full run — the 5,000-row queue timing test missed its 100 ms limit while a migration round trip ran alongside, then passed 3 of 3 alone (29.7 ms); ruff, mypy, eslint, tsc, locales (916 keys), prompt-audio check, bandit, semgrep (same packs), migrations head→base→head as in CI | Lesson: data steps in a downgrade must lift `FORCE ROW LEVEL SECURITY`, or they silently touch no rows. Also regenerated prompt audio for strings that changed in Phase 1 |
| 3.1 D4 missed calls: skip, recall, put back, no-show only after a miss | done | `0019_queue_recall.py`, `domain/queue/state.py`, `repositories.py` (`transition_visit`), `queue.py` (`/skip`, `/recall`, `/return`), `workers/main.py` (close-out) | unit state machine; integration: skip before/after grace, recall to original place, status page, no-show rules, day close-out labels | Grace period enforced by the server, set per hospital (`/admin/queue-settings`, Settings → Departments) |
| 3.2 D5 department checks on every queue action | done | `queue.py` (transitions, call-next, override, timeline) | integration: other department → 404 for 7 actions, call-next → 403, other hospital → 404 | |
| 3.3 D6 concurrent call-next | done | `repositories.py` (`lock_calls`, `call_next`), `queue.py` | integration with two database sessions: the second doctor waits, then gets the other patient | Board's "Call next" now lets the server choose |
| 3.4 D7 one patient per doctor; who called is recorded | done | `repositories.py`, `0019` (`called_by`) | integration: `DOCTOR_BUSY`, `CLAIMED_BY_ANOTHER_DOCTOR`, nurse calls without reserving | Board shows "Called by …" |
| 3.5 Visit timeline | done | `0019` (`visit_events`, append-only), `queue.py` (`/visits/{id}/queue-events`), `DoctorConsultation.tsx` | integration: event order and actors; grants checked (app cannot update/delete, worker can insert) | |
| 3.6 Self-reported danger sign marked "Nurse to confirm" | done | `repositories.py` (`queued_visits`), `StaffQueueBoard.tsx` | integration: marker cleared by acknowledging the alert | |
| 3.7 Screens and wording | done | `StaffQueueBoard.tsx` (missed-calls panel, countdown), `ConsultPatientRail.tsx`, `DoctorConsultation.tsx`, `PatientStatus.tsx`, `QueueSettingsCard.tsx`, locales (en/hi/mr) | 8 web unit tests; e2e `queue-recall.spec.ts` (status page, mark absent → recall, axe, 390 px width) | |
| 3.8 Queue policy document | done | `QUEUE_POLICY.md`, `PRIVACY.md` | — | Lists the decisions the hospital must sign off |
| 3.9 Staff screens and wording load separately | done | `router.tsx` (staff shell lazy), `locales/staff.ts`, `i18n.ts` | Lighthouse: performance 89–91 on `/kiosk` and `/s/demo` (85–88 before; budget 85) | Entry bundle 444.8 kB (140.8 kB gzip), down from 593.5 kB; staff strings are a 139.7 kB chunk loaded only for staff. Closes the Phase 1 lazy-loading follow-up |
| Phase 3 gate | green | — | API 870 passed / 1 skipped, core+domain coverage 96% (gate 95%); web 69; e2e 137 of 138 with CI's 2 workers — one kiosk preview screenshot test (a different one each run) missed its 5 s wait for the heading on the dev server under load and passes alone (27/27); PWA e2e; Lighthouse; ruff, mypy --strict, eslint, tsc, locales (965 keys), contrast, prompt-audio check, API types unchanged when regenerated as CI does, bandit, semgrep (same packs); migrations head→base→head, and the 0019 downgrade turns a real skipped visit into a no-show | CI retries a failed e2e test once; merged as PR #11 with CI green on the branch, the PR and `main` |
| 4.1 R7 AI draft the doctor accepts, edits or rejects; every sentence cites its answers | done | `domain/case_sheet.py` (`statements`, `review`), `ai/clinical_summary.py` (`sourced_statements`), prompt v2, `case_sheets.py` (`/ai-summary/review`), `pdf.py`, `DoctorConsultation.tsx` | 2 unit (citations limited to asked questions; guard per sentence); 3 integration (accept, edit then reject keeps the AI wording, department/role/version/no-draft refusals); 3 web unit; e2e `clinical-review.spec.ts` | Exit criterion met: a doctor checks a sourced draft against the transcript and accepts or rewrites it |
| 4.2 R3/R4 surgical and personal history; "don't know" and "prefer not to say" kept apart from "no" | done | bank `past_history.json` (`surgeries`), new `personal_history.json` (`habits`), `bank.py` (`unknown`, `declined`, `topic`), `engine.py`, `case_sheet.py` (`unanswered`, `social.other`) | 7 unit (`test_history_questions.py`); integration (kiosk sends the options as stand-alone choices); golden set regenerated (30/30) | The interview grows by two history questions (cap 15 → 17) |
| 4.3 R5 Ayurveda history | done | `0020_interview_settings.py`, bank `ayurveda.json` (appetite, digestion, bowel, sleep, exertion, diet), `case_sheet.py` (`ayush` + 16 examination fields), `admin.py` (`/admin/interview-settings`), `AyurvedaPanel.tsx`, `InterviewSettingsCard.tsx` | 4 unit; 3 integration (off by default, admin-only switch, kiosk asks it, vaidya saves findings, unknown field refused, audit); 4 web unit; e2e | Off by default; examination items are clinician-entry only, never asked of the patient; all wording drafted for vaidya and native-speaker review |
| 4.4 D10 answer corrections safe to retry | done | `repositories.py` (`next_correction_index`), `intake.py` | integration: fails before the fix with `uq_interview_turns_order`, passes after | Numbering comes from the stored turns under a per-visit lock |
| 4.5 R6 documents | deferred to Phase 6 | — | — | Upload, preview and review need a storage adapter and migration; the phase went to the exit criterion and the AYUSH module first |
| Phase 4 gate | green | — | API 888 passed / 1 skipped, core+domain coverage 96%; web 78; e2e 139 of 139 with 2 workers; PWA e2e; Lighthouse 89–91 (accessibility 100); ruff, mypy --strict, eslint, tsc, locales (1037 keys), contrast, prompt-audio check (24 new prompts), golden interviews 30/30 regenerated, red-flag/routing/extraction evaluations unchanged, case-sheet schema and API types regenerated, bandit, semgrep; migrations head→base→head | Entry bundle unchanged at 444.8 kB; staff strings chunk 156 kB |
| 5.1 Every record route isolated by hospital, department and patient | done | `test_access_isolation.py`, `synthetic.py` (an Orthopaedics nurse) | 5 integration: a route inventory that fails when a route naming a record has no probe; 19 visit and alert routes as another hospital and another department; 9 admin routes as another hospital; 19 patient-session routes as another patient (same and other hospital) and as the kiosk; staff routes with a patient session; the audio socket; queue, register, alerts, lookup, audit log and live board show no other hospital's or department's patients | Every refusal is "not found", changes nothing and leaves no entry in the owner's audit trail. Found that the session `DELETE` answered another hospital's id with 410 (fixed in 5.2) |
| 5.2 A patient or kiosk session cannot cancel a visit that has a token | done | `repositories.py` (`set_visit_status` changes only visits still registering), `intake.py` (`cancel_session`) | integration `test_session_safety.py` (failed before: 204 and the queued patient cancelled) | Reachable after a lost confirmation reply plus the kiosk's idle reset, or by anyone holding the phone's session for its two hours. Ending an unfinished check-in is now in the visit timeline (`session_ended`) and the audit log |
| 5.3 Answers fixed after confirmation, checked against the database | done | `intake.py` (`_require_registering` on answers, corrections and Back) | integration (failed before: after a lost confirmation reply, a new main problem reopened the interview and set the queued visit back to `registering`, off the board) | `QUEUE_POLICY.md` already promised this; it was enforced only by the Redis session |
| 5.4 Scenarios 5 and 10: an answer sent twice is saved once | done | `intake.py` (`_session_lock`, `_already_answered`, `_current_interview`), `KioskIntake.tsx`, locales (`connectionRetry`) | integration: retry after a lost reply, concurrent double tap, last answer replayed after the summary (failed before: 422 "answer does not match the current question", in English, and the kiosk stuck); 1 web unit | One change at a time per session (Redis lock, 20 s); the kiosk says "tap again, your answers are kept" instead of "Failed to fetch" |
| 5.5 Scenario 14: only real photos of a sensible size reach the model | done | `speech.py` (signature must match the declared type, 1 MB limit, checks before the AI provider), `intake.ts`, `KioskIntake.tsx`, locales (`scanTooLarge`, `scanNotPhoto`) | integration: PDF, PDF named as a photo, empty, over the limit, over the request limit, a real photo, 5 per visit then 429 (failed before: the PDF reached the model); 1 web unit | The prescription photo is the only upload today; document upload (R6) adds its own cases in Phase 6 |
| 5.6 "Call staff" limited; R12 session data cleared after submission | done | `intake.py` (`request_staff_help`, `_queue_receipt`, `confirm_summary`), `PRIVACY.md` | 2 integration (both failed before) | 5 presses per visit in 10 minutes, none after the visit finished. After confirmation Redis keeps only visit, kiosk, channel and language; the answers, status link, PIN and AI follow-up drafts leave Redis, and a repeated confirmation rebuilds the receipt from the database |
| 5.7 An open staff board stops when its sign-in ends | done | `queue.py` (the staff socket re-checks the account every minute and closes at sign-in expiry), `StaffQueueBoard.tsx` (renews the sign-in on close code 4401) | integration: expiry → 4401, deactivation → 4403; 1 web unit (failed before) | A deactivated nurse's open screen kept receiving patients' red-flag words |
| 5.8 The fifteen scenarios | done | `docs/E2E_SCENARIOS.md`, `test_scenarios.py` | 7 integration (3, 5 and 10, 8, 9 down and hanging, 14, 15); the others mapped to existing tests | 15 of 15 covered; 8 defects found, each with a test that failed before its fix |
| 5.9 Load smoke | done | `tests/perf/test_load_smoke.py`, `make load-smoke` | On the running stack (Docker Desktop on this PC, gunicorn with 2 workers behind nginx, AI off): 40 patients arriving within 10 s finished in 14.1 s with 0 failed requests and every step's p95 at or under 107 ms (answers 105 ms, confirmation 86 ms). Stress at 150 patients in 10 s: 0 failed requests, every token unique and in order, answers p95 1.7–2.0 s, confirmation p95 3.0 s | A busy OPD sees well under one new patient per second; the smoke runs four a second. Confirmation is the slowest step because token numbers are issued one at a time per department |
| 5.10 R9 consent purposes | moved to Phase 6 | — | — | Grouped with document upload (R6), which needs its own consent purpose |
| Phase 5 gate | green | — | API 905 passed / 2 skipped (the load smoke skips without `LOAD_SMOKE_URL`), core+domain coverage 96%; web 81; e2e 139 of 139 with 2 workers; PWA e2e; Lighthouse 89–92 (accessibility 100); ruff, mypy --strict, eslint, tsc, locales (1040 keys), contrast, prompt-audio check, golden interviews, red-flag/routing/extraction/adversarial evaluations unchanged, case-sheet schema unchanged, API types regenerated (endpoint descriptions only), bandit, semgrep (327 rules, 0 findings); 10 of 10 live checks of the fixes on the rebuilt stack | No migration. Entry bundle 446.1 kB (+1.3 kB, three new strings in three languages); staff chunk 156.5 kB. The demo database was still at 0019 and was migrated to 0020 before the rebuilt API went live |
| 6.1 R9 consent by purpose: AI help optional and revocable | done | `0021_consent_purposes.py`, `consent/ai-{en,hi,mr}.md`, `api/v1/ai_gate.py`, `intake.py` (`/consent/ai`), `speech.py`, `audio.py`, `queue.py` (`/status/{token}/ai-consent/withdraw`), `case_sheets.py` (`/visits/{id}/ai-consent/withdraw`), `KioskIntake.tsx`, `PatientStatus.tsx`, `DoctorConsultation.tsx`, locales | integration `test_consent_purposes.py`, plus scenario, audio-socket and case-sheet tests that count the calls reaching the Gemini stub (none without a "yes"); web unit; e2e `consent-purposes.spec.ts` and the local-voice read-back in `phase-10.spec.ts` | Without a "yes": no language-model call, no online or browser speech recognition, the read-back only with an on-device voice. The status link can only stop AI help, never start it. Withdrawal hides the AI draft (also on earlier-visit summaries) and blocks its review |
| 6.2 R6 old papers: capture, encrypted storage, AI reading as a draft, the doctor's decision, printed-range flags | done | `0022_documents.py`, `core/crypto.py` (`encrypt_bytes`), `domain/documents.py`, `ai/document_reading.py`, `api/v1/documents.py`, `api/v1/visit_access.py` (department and under-care rules shared with history), `repositories.py` (`DocumentRepository`), `scripts/retention.py`, `KioskPapers.tsx`, `DocumentsPanel.tsx`, `visitDocuments.ts`, `lib/photos.ts`, locales | 37 unit (`test_document_rules.py`: range flags incl. Indian digit grouping, nothing flagged from a guess, printed dates, file signatures, the reading's shape); 8 integration (`test_documents.py`: photo encrypted at rest, limits, invalid files, AI only with consent counted at the stub, review by doctors only and changeable, withdrawal hides the reading, failed and malformed readings, staff photos only while under care, earlier papers and another patient's paper); isolation probes for the 7 new routes; retention deletes papers and photos; 11 web unit; e2e `old-papers.spec.ts` (kiosk capture through the real file chooser, the doctor's review, axe, 390 px) | Without AI consent the photo is kept unread and nothing leaves the server. Results are compared only with the range printed beside them, by arithmetic; text results, censored values and multi-group ranges are never flagged |
| 6.3 Demo data, synthetic papers, pre-demo check, rehearsal script | done | `app/demo_papers.py`, `scripts/make_demo_papers.py`, `docs/demo-papers/`, `cli.py` (`seed-demo-returning [--reset]`, `clear-demo-returning`, `ai-check`), `start-demo.ps1`, `scripts/demo-check.ps1`, `docs/DEMO.md`, README | 2 unit (`test_demo_papers.py`), 3 unit (`test_ai_check.py`), 1 integration (`test_demo_seed.py`: the card links today's visit, history and the earlier paper show, reset keeps the card, clear removes everything) | The returning patient (fictional, 52, OPD card printed by `start-demo`) has an earlier visit whose prescription is kept as an unread photo, so no AI output is ever faked; the presenter photographs the synthetic test report and Gemini reads it live. `demo-check` asks Gemini and Sarvam for one tiny answer (no patient data) |
| 6.4 Polish found by rehearsing | done | `KioskIntake.tsx` (the optional AI question's two answers styled alike), `StaffQueueBoard.tsx` (a danger-sign alert says what the patient tapped, not "DS_CHEST"), `vite.config.ts` (`autoUpdate`) | 1 web unit; PWA offline e2e; live: an open tab moved to the new build after one reload (before: it stayed on the old build until every tab closed) | All three came from rehearsals on the live stack, none from the tests |
| 6.5 Two rehearsals from a cold start | done | — | Cold start 1: `docker compose down`, then `start-demo.ps1` in 61 s; `demo-check` all green (Gemini 2.2 s, Sarvam 0.9 s); kiosk in Marathi with the OPD card and the test report (MED-001); doctor: cited AI brief, earlier visits, the report read by Gemini with the printed-range flags, accepted, finished. Cold start 2: 80 s; `demo-check` green; phone check-in in Hindi at 390 px with the prescription (MED-002, no sideways scroll); kiosk emergency (chest discomfort → MED-003 first in line, urgent, "Nurse to confirm"); the prescription read and accepted | No console errors or failed requests apart from the sign-in page's expected session check (401). Leftovers removed: `--reset`, the rehearsal kiosk deleted, the alert acknowledged |
| Phase 6 gate | green | — | API 965 passed / 2 skipped, core+domain coverage 96%; web 96; e2e 144 of 144 with 2 workers; PWA e2e; Lighthouse 89–93 (accessibility 100); ruff, mypy --strict, eslint, tsc, locales (1134 keys), contrast, prompt-audio check, golden interviews and evaluations unchanged, case-sheet schema unchanged, API types regenerated, bandit, semgrep (0 findings); migrations head→base→head with 0021 and 0022; live: documents 20/20 with Gemini, two cold-start rehearsals | Entry bundle 455.5 kB (+9.4 kB for the kiosk's old-papers section in three languages); staff chunk 172.6 kB (+16.1 kB). The demo database was migrated to 0022 before the rebuilt API went live |
