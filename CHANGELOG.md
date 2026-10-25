# Changelog

What changed in each release of OPD Saathi, in plain language for hospital administrators and
IT, then the technical detail. Versions follow semantic versioning; `1.0.0` is cut only after
the go-live gate (`docs/GO_LIVE_CHECKLIST.md`). How releases are made and installed:
`docs/RELEASES.md`.

## [Unreleased]

### For hospital staff and administrators

- **The doctor's own notes** (Clinical notes tab): examination findings, assessment, plan and a
  follow-up date, written by the doctor and never by AI. Each save is a new version; earlier
  versions stay readable, and the latest is printed on the case sheet PDF.
- **Review of systems** (Settings → Patient interview, off by default): one extra kiosk and
  phone screen, "In the last month, have you had any of these?", with one choice per body
  system, leaving out systems the interview already asked about. Ticked systems reach the
  case sheet; "None of these" is recorded as a denial of each system shown. Wording awaits
  clinical and native-speaker review.
- **Results over time** (Old papers tab): test results a doctor accepted, from this and earlier
  visits, oldest first, each compared with the range printed on its own paper. Readings still
  awaiting a decision are counted but never shown there.
- **Lost OPD card** (Patients, for nurses and administrators): find the patient, check age and
  sex with them, and give a new PIN. The number and records stay the same; the old PIN stops
  working at once.
- **Online AI help switch** (Settings → Departments): administrators turn AI off or on for the
  hospital. The card says when no choice is saved yet (AI is on where the server has it) and
  when the server has no AI, so the switch does nothing.
- **Who is answering** (kiosk, phone and desk): the consent screen asks whether the patient, a
  parent or guardian, or someone helping is answering; for a child the details screen asks
  again and needs a choice. The doctor's screen and the case sheet PDF say when someone else
  gave the history. The offline short form asks it too.
- **Features for patients** (Settings → Departments): SMS queue messages are now off until an
  administrator turns them on, because they need an SMS provider and registered message
  templates; while off, kiosks and phones do not offer the SMS box. **After upgrading, SMS is
  off** until it is switched on again. Phone check-in can be turned off for the whole hospital
  at once; kiosks keep working.
- **Privacy notice for patients**: the full notice (what is collected, why, who sees it, how
  long it is kept, the patient's rights and where to complain) in English, Hindi and Marathi,
  linked from the token page and phone check-in. Settings → Departments → Privacy notice:
  the grievance officer's contact, and a poster with a QR to print for the waiting area. The
  notice is a draft until the hospital's counsel approves it, and says so.
- **The queue board in Hindi and Marathi**: the urgent-alert "Acknowledge" button and its
  wording, filters, columns, the next-to-call panel and every error were English only.
- **Staff cards and patient signs to print** (`docs/print`): one page each for nurses,
  doctors and administrators, "How to check in" and "If you feel very unwell, tell the staff
  now", in three languages, using the screens' own words.
- **Fixed:** changing your password while the page renewed its sign-in could sign you out
  with an error.
- **Fast on a hospital with years of visits**: the visit register, the audit log search by
  record or by person, and the end-of-day close-out were measured on a million visits and no
  longer slow down as the history grows.
- **Language buttons named as they read**: the kiosk's language buttons are announced as they
  look ("हिन्दी Hindi"), so someone using voice control can say either word.
- **Fixed:** three icons sat on a line of their own above their words: "Earlier versions" of
  the doctor's notes, the "Results over time" heading, and the privacy card's "Open the notice"
  and "Poster with QR" (now buttons like the kiosk panel's).
- **Feature guide for the team** (`docs/OPD-Saathi-Feature-Guide.pdf`), rewritten: 18 pages
  instead of 32, what is new since 11 October marked, with how each feature works and what it
  improves, what the system cannot do, and current screenshots.
- **A logo of its own**: a plus that speaks, with a voice wave at its centre, for care,
  conversation and voice. It replaces the placeholder icon in the browser tab, on the phone or
  tablet home screen, on the loading screen and in the staff sidebar, and the "O" in the kiosk
  header (a hospital's own logo, when one is set, still shows there instead).

### Technical

- `GET`/`PUT /visits/{id}/doctor-notes` with `If-Match` versions; table `doctor_notes`
  (migration `0029_doctor_notes`, insert-only for the application, encrypted text included in
  key rotation and restore drills).
- Question bank module `review_of_systems` (16 modules); `PATCH /admin/interview-settings`
  changes only the switches it is sent; migration `0030_review_of_systems`. The golden
  interview set includes one review-of-systems case per language.
- `GET /visits/{id}/investigations`: accepted readings only, built on the server; earlier
  visits follow the same "under care" rule as the papers list; audited.
- `POST /patients/card/reissue` moves `patients.card_version` on (the PIN is derived, never
  stored); the version from `/patients/lookup` guards against re-issuing twice. Audited
  without the PIN.
- `/admin/ai-settings` also returns `chosen` and `available`.
- `GET`/`PATCH /admin/feature-settings`: `sms_messages` (default off), `phone_checkin`
  (default on) and `sms_provider` (whether the server can send at all); table
  `hospital_feature_settings` (migration `0032_feature_switches`; the worker may read only the
  SMS switch). With SMS off, sessions carry no `sms_consent`, a tick from an older screen is
  ignored and no message is queued; with phone check-in off, every check-in QR answers
  `409 CHECKIN_TURNED_OFF`. Changes are audited (task 7.14.7).
- `visits.answered_by` and `consents.given_by` (migration `0031_answered_by`); `answered_by`
  on the consent, identity (required under 18: `ANSWERER_REQUIRED`) and offline submission
  payloads; forms queued by older kiosks are recorded as the patient's own.
- Decisions recorded: no PgBouncer yet, with a connection budget and a test that keeps
  transaction pooling possible (ADR 013); patients do not view records in v1 (ADR 014); no
  drug-interaction checks (ADR 015). New: `docs/LIMITATIONS.md`, `docs/RAG_DESIGN.md`.
- Web coverage gate in CI (`npm run test:coverage`, `@vitest/coverage-v8`, approved by the
  owner): every source file counts; thresholds at the level reached (statements 56%,
  branches 52%, functions 51%, lines 58%), to be raised, never lowered.
- `GET /visits/{id}/fhir`: the visit as a FHIR R4 bundle (patient, encounter, patient-reported
  complaint and conditions as unconfirmed, allergies, medicines as stated, accepted results
  with their printed ranges, the interview, papers, consents, and who answered), tagged
  not-ABDM-certified; doctors and administrators; audited (ADR 017).
- Failure drills nightly (`scripts/failure-drills.sh`, workflow `nightly-failures.yml`):
  Redis, PostgreSQL and the worker stopped and the data disk filled on the production-mode
  stack; each shows its known state and raises its alert, then recovers. The first run found
  that `RedisDown` could never fire: the worker exited when Redis went away, so monitoring saw
  `WorkerDown` instead and the Redis gauge went stale. The worker now stays up through a Redis
  outage, reports it, and resumes when Redis is back.
- `GET /privacy-notice/{hospital_id}?language=` (public, rate-limited): the hospital's name,
  the notice (`app/domain/privacy/notice-*.md`, versioned and reviewed like the consent copy,
  so `CLINICAL_CONTENT_APPROVAL_REQUIRED` refuses a draft) and the grievance contact.
  `GET`/`PUT /admin/privacy-settings` (migration `0033_privacy_contact`, audited); the status
  and phone check-in responses carry `privacy_path` (task 7.5.2).
- Session renewal locks the account before its refresh token, the order sign-in, password
  changes and "sign out everywhere" use; the reverse order deadlocked with a password change
  (500). A token ended on purpose no longer counts as token reuse in the audit log.
- End-to-end against the real API (task 7.13.2): `apps/web/e2e-stack`, run by the
  production-smoke CI job on the production-mode stack: two-step sign-in, kiosk pairing and a
  whole check-in, a new doctor's first sign-in, call, consultation and notes, the privacy
  notice.
- Upgrade test (tasks 7.13.3, 7.7.9): `nightly-load.yml` installs the previous release with a
  patient and a paired kiosk, upgrades in place and checks with `scripts/upgrade_check.py`
  that data, keys, sign-in and the kiosk survive.
- `scripts/make_print_pack.py` writes `docs/print`; CI fails when it is stale. The operator
  guide (`docs/DEPLOYMENT.md`) gains a check of the installation and sends Windows Server to a
  Hyper-V Linux virtual machine (there was never a `setup-hospital.ps1`).
- Index review on a million visits (task 7.8.4; `docs/SCALING.md`,
  `services/api/tests/perf/index_review.py`): migration `0034_index_review` adds partial
  indexes on open visits and on numbers kept for queue messages, the register by time, and the
  audit log by record and by person. The close-out goes from 395 to 9 ms, the register
  newest-first from 266 to 12 ms, audit search by record from 100 to 11 ms. The upgrade builds
  them in about 5 seconds per million visits. Query-count gates for the queue, case-sheet and
  report screens (`tests/integration/test_query_counts.py`) fail on a query per row or a
  statement over budget. Measured and left for task 7.8.5: the 5-minute analytics rollup
  refresh recomputes the whole history (10 s at a million visits).
- Tests (task 7.0.6): ten consecutive full browser runs with two workers and no retry. Fixed
  on the way: the AI-switch browser test's mock crashed the privacy card beside it (CI was red
  from the privacy notice commit); a results-over-time test took its date from the runner's
  UTC clock and failed every evening from 18:30 UTC; the Lighthouse gate judges each page's
  median of three runs instead of failing on one noisy run, with the same thresholds.
- Dependencies, tested together: @tanstack/react-query 5.104.5, eslint-plugin-react-refresh
  0.5.7, TypeScript 5.9.3 (still pinned to patch releases; it found the one-tab-at-a-time lock
  helper returning a promise of a promise), @axe-core/playwright 4.14 (its label-in-name check
  found the language buttons' names), and the CI-only Semgrep lock regenerated (pydantic 2.14.1
  with pydantic-core 2.50.1, bracex 3.2). Not taken: jsonschema 4.26, wrapt 2.5 and
  exceptiongroup 1.3, which Semgrep caps; @vitejs/plugin-react 6, which needs Vite 8.
- The feature guide is built from `docs/feature-guide-screenshots/guide.html` by
  `render-pdf.mjs` beside it, which refuses to write the PDF when a section would spill onto a
  second page. `docs/LIMITATIONS.md` and `docs/SCALING.md` corrected (the FHIR export exists;
  the analytics rollup is the one slow frequent job).
- Brand icons in `apps/web/public`: `saathi-icon.svg` (the app icon), `saathi-mark.svg`,
  `favicon.svg` (without the voice wave, for 16 and 32 px tabs) and `saathi-icon-maskable.svg`.
  The PNGs for Android and iOS (`saathi-icon-192.png`, `saathi-icon-512.png`,
  `saathi-icon-maskable-512.png`, `apple-touch-icon.png`) come from `scripts/render-icons.mjs`;
  the PWA manifest lists them beside the SVG.

## [0.7.0]

The production-readiness release (Phase 7, gates G0 to G2). For pilots with synthetic data and
staging; not yet for real patients (clinical sign-off, penetration test and the go-live
checklist are still open).

### For hospital staff and administrators

- **SMS only by the patient's own choice.** The SMS box on the kiosk is never pre-ticked, and
  a patient can stop messages from their status page.
- **Staff accounts.** Administrators reset passwords (the person chooses a new one at first
  sign-in), unlock accounts, edit staff and departments, and sign a person out everywhere.
  Everyone can change their password and see and end their own sign-ins (My account).
- **Two-step sign-in** with an authenticator app is required for administrators and available
  to doctors and nurses, with ten single-use recovery codes.
- **Audit log screen** (Settings → Audit log): search by record, person, action and date, and
  export for an investigation.
- **Kiosks stay paired**: their device secrets renew by themselves every 30 days.
- **Settings → System** shows the version, the last backup, disk space, the certificate,
  whether the database, queue store and worker answer, each kiosk's last check-in, and how AI,
  voice and SMS are set up.
- **Hindi and Marathi** staff screens: the recent-visits list and visit statuses showed
  "???"; they are translated again. Staff initials in the sidebar are correct for every name.
- **Screen readers** hear a busy button as "Save working", not as one joined word.

### For hospital IT

- **Installation on a Linux server** with one command (`scripts/setup-hospital.sh`): secrets as
  files, HTTPS only, hardened containers, starts at boot. Guide: `docs/DEPLOYMENT.md`.
- **Backups that restore to the minute**: changes are archived within 5 minutes, encrypted
  backups are taken every night, optionally copied off-site; a restore drill proves them.
  `docs/runbooks/restore.md`.
- **Alerts to a person** by email (and Telegram, if wanted) when the server, database, disk,
  backups, certificate or a kiosk needs attention, or an urgent patient alert waits more than
  5 minutes. `docs/runbooks/alerts.md`.
- **Signed releases**: images are scanned, signed and listed with their components; an offline
  bundle serves hospitals with slow internet.
- **Paper fallback pack** to print (`docs/paper/`): intake forms in three languages with the
  kiosk's own questions, a token roll and a kiosk sign.
- **Logs kept 180 days** in the server's journal (CERT-In), with no patient data in them.
- Runbooks for every failure, the key escrow and rotation, the paper fallback, and the
  go-live checklist.

### Technical

- Worker sends SMS with its own notify key, never the patient-data key; worker batches survive
  one bad row; unread settings removed or wired; every image pinned by digest.
- Production overlay (`docker-compose.prod.yml`) with TLS in nginx, edge rate limits, strict
  CSP and cross-origin isolation; production smoke test in CI.
- Key rotation: JWT key overlap, data-key ring with `app.cli reencrypt` and
  `app.cli verify-decrypt`.
- Least privilege proven by tests; PostgreSQL and every service run as non-root users.
- Security in CI: Trivy (repository and every image), Bandit, Semgrep, pip-audit, npm audit,
  Gitleaks; nightly signed-in OWASP ZAP and Schemathesis; a test that logs carry no patient
  data. OWASP ASVS L2 self-assessment: `docs/security/ASVS.md`.
- Clinical content can be marked approved only with a review record;
  `CLINICAL_CONTENT_APPROVAL_REQUIRED` blocks start-up while drafts remain.
- Prometheus metrics (multiprocess) and Alertmanager with 19 tested alert rules; pgBackRest
  point-in-time recovery; k6 load and soak tests.
- Database migrations up to `0028_worker_monitoring` (expand-only; the previous release runs
  on this schema).
- Web app on React 19, jsdom 30 and Playwright 1.64. TypeScript stays on 5.7 until
  typescript-eslint supports TypeScript 7.
