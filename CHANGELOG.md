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
- `visits.answered_by` and `consents.given_by` (migration `0031_answered_by`); `answered_by`
  on the consent, identity (required under 18: `ANSWERER_REQUIRED`) and offline submission
  payloads; forms queued by older kiosks are recorded as the patient's own.
- Decisions recorded: no PgBouncer yet, with a connection budget and a test that keeps
  transaction pooling possible (ADR 013); patients do not view records in v1 (ADR 014); no
  drug-interaction checks (ADR 015). New: `docs/LIMITATIONS.md`, `docs/RAG_DESIGN.md`.
- Web coverage gate in CI (`npm run test:coverage`, `@vitest/coverage-v8`, approved by the
  owner): every source file counts; thresholds at the level reached (statements 56%,
  branches 52%, functions 51%, lines 58%), to be raised, never lowered.

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
