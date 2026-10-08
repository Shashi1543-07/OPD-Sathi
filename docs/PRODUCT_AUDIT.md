# OPD Saathi product and UI traceability audit

**Audit date:** 7 October 2026  
**Scope:** F1–F15, kiosk/patient, nurse/reception, doctor, admin, and Judge Mode.  
**Data:** synthetic records only.

## Backend to frontend traceability

| Capability | Backend implementation | Intended user | Frontend screen | User-facing representation | States checked | Status |
|---|---|---|---|---|---|---|
| F1 language and kiosk | language-aware prompts and intake session language | Patient | `/kiosk` | large language tiles, accessible controls, translated shell | selected, default, offline banner | Represented; Judge Mode ribbon now follows selected language |
| F2 consent and identity | consent gate, encrypted visit identity, audit events | Patient, nurse | kiosk intake; assisted registration | consent before intake; staff-assisted registration requires consent | consent, input validation, error | Represented |
| F3 multilingual speech | STT provider interface, mock/live adapters, transcript events | Patient | kiosk interview | voice capture, partial/final transcript, typed/touch fallback | recording, partial, final, unavailable fallback | Represented; demo-safe fixtures are synthetic |
| F4 adaptive interview | deterministic interview engine and bounded question selection | Patient | kiosk interview | one question at a time with progress and back/skip choices | loading, answer, retry/fallback | Represented |
| F5 structured case sheet | case-sheet domain, extraction, versioned edit API | Doctor, nurse | consultation and nurse case review | structured history, source/confidence, editable fields | loading, empty, save, conflict, unavailable | Represented |
| F6 patient read-back | read-back/TTS adapters and confirmation endpoint | Patient | review and confirm | spoken and visible summary with correction/confirm actions | playback, confirm, retry/fallback | Represented |
| F7 red flags | deterministic red-flag rules, durable events, queue/WebSocket notifications | Nurse, doctor | staff queue and case sheet | prominent staff alert with reason and acknowledgement | active, acknowledged, reconnecting | Represented; patient copy remains neutral |
| F8 department routing | deterministic routing rules and department assignment | Patient, staff | token handoff and queue | assigned department and reasoned staff override | assigned, override with reason, failure | Represented |
| F9 token and live queue | queue ordering, status token, WebSocket, notification outbox | Patient, nurse, doctor | token/status page and staff queue | token, position, called/in-service states, live indicator | live, reconnecting, empty, offline | Represented; SMS/WhatsApp depend on configured adapter |
| F10 doctor workspace | consultation state API, case-sheet and PDF endpoints | Doctor | `/staff/queue`, `/staff/consultations` | live queue, selected case, transcript, edit, start/finish, PDF | loading, empty selection, edit conflict, export error | Represented through queue and consultation workflow; no separate doctor landing dashboard |
| F11 nurse/staff console | registration, override, alert acknowledgement, call-next/no-show, auth | Nurse, reception, admin | `/staff/queue`, `/staff/patients`, `/staff/reports`, `/staff/settings` | role-aware navigation and operational actions; admin lands on analytics overview | loading, empty queue, error, role denial, expired session | Represented; the unfinished admin placeholder route now redirects to Reports |
| F12 prescription upload | document metadata and encrypted object storage adapter | Patient, nurse, doctor | intake document step and case sheet | attach an existing prescription/report; staff can review metadata | upload progress, validation, failure | Represented; production object-store wiring must be configured |
| F13 analytics | tenant-scoped analytics API, rollups, CSV export | Admin | `/staff/reports` | volume, waiting/consultation time, department and language trends | loading, no data, export, error | Represented |
| F14 offline operation | PWA cache, IndexedDB outbox, idempotent replay | Patient, kiosk | kiosk offline intake and sync indicator | offline status, short-form offline intake, pending/synced state | offline, pending, replay, conflict/failure | Partially represented: short-form intake/replay works; scripted voice still requires local API and is not fully offline |
| F15 privacy/security | tenant RLS, auth/role checks, encryption, audit log, rate limits | All roles; admin for audit | staff sign-in, kiosk activation, admin audit/API | secure sign-in, one-time activation, neutral patient copy | denied, lockout, rate limit, expired session | Represented at appropriate user level; internals stay out of patient UI |
| Service health and recovery | bounded readiness checks, metrics, provider fallback | Staff/admin/operator | connection status, error states; operator endpoints | live/reconnecting/offline status and actionable retry | ready, dependency unavailable, recovered | Represented in UI where relevant; detailed metrics remain operator-facing |

## Role and information architecture review

### Patient and kiosk

- The welcome page identifies the clinic, presents language selection and the next step, and keeps activation details separate from the patient preview.
- The kiosk shell keeps the safety disclaimer visible and provides help, larger text, high contrast, read-aloud, and idle-session reset.
- Offline messaging is explicit. It must not imply that the full voice workflow continues without the local API.
- Screenshots showed the Judge Mode ribbon was English in Hindi and Marathi. It now uses locale strings and has a regression test.
- A same-origin, optional hospital logo slot and a first-paint splash screen were added. The default brand mark remains when no hospital logo is configured.

### Nurse and reception

- The queue puts urgency and alert acknowledgement in the staff workflow and provides department/urgency overrides with a reason, call-next, and no-show actions.
- Empty queue and loading states are present. Connection state is visible in the staff header.
- Judge Mode’s “How it works” panel now has locale strings; clinical and native-speaker review remains pending.

### Doctor

- Consultation opens a structured case sheet with transcript, source/confidence indicators, edit and conflict recovery, consult state actions, and PDF export.
- `/staff/dashboard` redirects to `/staff/queue`; the live queue is the doctor landing destination, with consultation actions one click away. There is no separate doctor dashboard page.

### Admin

- Role-aware navigation exposes staff accounts, kiosks, departments, analytics, and audit API functionality.
- Empty/loading/error and pagination affordances are present in the administration UI. High-impact server actions remain role-protected.

## Prioritized issues

| Priority | Finding | Resolution/status |
|---|---|---|
| P0 | Judge safety framing was not localized | Fixed with `judgeRibbon` translations and a test |
| P1 | Doctor has no separate dashboard landing page | Addressed by making the live queue the doctor landing destination; consultation actions remain one click away |
| P1 | Screenshot generator covered only the kiosk welcome at one viewport | Addressed for acceptance evidence: saved kiosk EN/HI/MR and patient, nurse, doctor, and admin role-flow captures under `docs/screenshots/phase-14/` |
| P1 | In-app browser bridge returned a missing `sandboxPolicy` error during this handoff | Automated browser suite and saved screenshots are used as supporting evidence; interactive in-app review remains unverified |
| P2 | Hospital branding had no configurable image slot or initial app splash | Added optional `VITE_HOSPITAL_LOGO_URL` and static first-paint splash |
| P2 | Demo panel copy was hard-coded in English | Localized in the staff namespace |

## Evidence and limits

- Visual inspection: kiosk EN/HI/MR plus patient summary, nurse queue/consent, doctor consultation/case sheet, and admin kiosk/analytics captures are saved in `docs/screenshots/phase-14/`. The three kiosk welcome images were generated before the isolated API was connected and show the offline banner; the role-flow captures came from the full Playwright run using synthetic records. The nurse queue capture includes a transient reconnect state and long-aged synthetic fixture, so it is evidence for state review rather than a polished demo frame. The in-app bridge itself remained unavailable, so these do not count as interactive in-app browser inspection.
- Automated acceptance: 116/116 Playwright E2E and 761 API tests passed on the isolated Phase 14 stack; contrast and production-preview Lighthouse gates passed. Exact measurements and test limits are in `docs/phase-reports/phase-14.md`.
- Automated checks are reported in `docs/phase-reports/phase-14.md` and `docs/EVALUATION.md` with exact output and known limitations.
- Rule/routing content, prompts, consent, and translations are drafts requiring clinician/native-speaker review. Synthetic fixture scores do not measure clinical accuracy or live-model quality.
- This audit does not claim production readiness. Production image smoke, manual offline acceptance, and the complete role/viewport screenshot set remain open.
