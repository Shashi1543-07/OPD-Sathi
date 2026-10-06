# Phase 7 — Doctor and nurse consoles, PDF (2026-10-06; `phase-07-consoles`; `059fd38`)

**Status:** Phase 7 implementation and local acceptance gates are complete and merged into `main` as `16d4952` through PR #5. The final hosted backend, frontend, and security checks passed on `14bc2df`. Backend-wide coverage is **80.0% (2,548/3,184 statements)**, meeting the playbook's **80%** target. Phase 6 is integrated into `main` as `0c833c3`; all Phase 7 test data is synthetic.

## Completed work

### 7.1 Staff navigation and route guards

- Staff navigation is role-aware for admin, doctor, and nurse routes.
- The role/page matrix covers all **18** combinations.
- Vitest, TypeScript typecheck, ESLint, locale parity, and production build passed when 7.1 was recorded.

### 7.2 Staff queue board

- The board selects departments from the caller's authorized active departments and offers waiting, called, and in-consult filters.
- The API returns token, age, sex, chief complaint, urgency, queue position, waiting time input, and current status.
- Urgent rows pin above other active rows. TanStack Virtual renders visible rows; the signed cursor API supplies pages of **50** rows.
- Queue websocket events use monotonic `seq` values: duplicate/old events are ignored, contiguous deltas update loaded rows, and sequence gaps trigger a fresh page. Unknown upserts trigger a refresh.
- Connection state, alert acknowledgements, and the persisted alert-sound preference are visible in the staff board.
- Doctors and nurses are restricted server-side to their assigned department. Admins can choose among active departments. Alerts use the selected department as well.

### 7.3 Audited case-sheet API

- Doctors and nurses can read case sheets only for visits in their assigned department; each read is audited.
- Doctors can edit only history fields through schema-validated JSON Patch operations. Routing, red flags, provenance metadata, and the history disclaimer are protected.
- Writes require the current `If-Match` version, lock and compare the row, increment the version, mark changed fields as doctor-confirmed, and audit operation/path metadata without recording clinical values. Stale edits return **409** and missing preconditions return **428**.

### 7.4 Doctor consultation view

- Doctors can open an active visit from the queue and review the case sheet in reading order, with source/confidence details and editable history fields.
- Stale writes open a reload/reapply dialog. The screen includes audited transcript reads, Start/Done actions, print support, and N/E/P keyboard shortcuts.
- Broader role-matrix and two-browser edit checks remain in the Step 7.9 acceptance pass.

## Verification recorded

- Frontend tests: **25 passed** across **7** test files.
- Queue access policy tests: **3 passed**.
- Case-sheet API and patch tests: **13 passed**.
- Typecheck, ESLint, production build, Ruff, strict mypy, locale parity (**254 keys per language**), and contrast checks (**12 pairs**) passed.
- Production bundle after the 7.4 screen: **455.77 kB** JavaScript (**143.65 kB gzip**); build completed successfully.
- The Phase 5 staff-call Playwright scenario passed locally after fixing virtualizer ref initialization and the query-aware queue mock.
- Hosted PR #5 CI passed all required jobs (backend, frontend, security) at `c1bca98`: [Actions run](https://github.com/Shashi1543-07/OPD-Sathi/actions/runs/37408601882).
- `git diff --check` passed.

The complete local backend suite was also attempted: **318 passed**, one Redis websocket latency integration failed to subscribe in this environment, and three tests could not create their temporary directories under the default Windows pytest temp root. The hosted CI backend job passed on the same queue API changes.

- Hosted PR #5 CI passed backend, frontend, and security after the 7.3 Bandit fix at `fee4776`: [Actions run](https://github.com/Shashi1543-07/OPD-Sathi/actions/runs/37410094888).
- Current local Phase 7.4 gates: frontend **25 tests passed across 7 files**; case-sheet/transcript API **13 tests passed**; typecheck, ESLint, locale parity (**254 keys per language**), production build, Ruff, strict mypy, Bandit, and `git diff --check` passed.
- Hosted PR #5 CI passed backend, frontend (including E2E, Lighthouse, and generated-type parity), and security at `c990f49`: [Actions run](https://github.com/Shashi1543-07/OPD-Sathi/actions/runs/37411307425).

### 7.5 Nurse console

- Nurses can start assisted registration in English, Hindi, or Marathi. The existing consent screen is shown first; demographic identity fields are submitted only after consent, and the patient's name is required for assisted registration.
- The API verifies the active nurse's active department assignment, creates a hospital-scoped session with no real identity prefilled, and records consent channel `nurse`. The audit trail records the nurse and visit action without including submitted identity values.
- Existing nurse queue tools handle red-flag acknowledgement, route confirmation, reasoned urgency override, and no-show. API access is constrained to the nurse's active department.
- Verification: `pytest services/api/tests/unit/test_intake.py -q` — **5 passed**; `npm test -- --reporter=dot` — **26 passed across 8 files**; Ruff, strict mypy, Bandit, frontend typecheck, ESLint, locale parity (**269 keys per language**), production build, and `git diff --check` passed.

### 7.6 Administrator console

- The administrator manages hospital departments, staff accounts, and kiosks. Activation codes are held in the current screen state; retrying an idempotent kiosk create cannot replay its secret.
- Verification: frontend **27 tests across 9 files**, strict TypeScript, ESLint, locale parity (**310 keys per language**), 53 domain-data API tests, Ruff, and strict mypy passed.

### 7.7 Multilingual case-sheet PDF

- Added audited, department-scoped `GET /visits/{id}/case-sheet.pdf`, an A4 template with English, Hindi, and Marathi labels, token and attention reasons, history sections, and the history-only footer. The doctor screen downloads through authenticated API requests.
- Verification: PDF/API **7 tests passed**; extracted text contained the synthetic Hindi complaint, queue number, attention reason, and disclaimer. `pdfinfo` reported **1 A4 page**. Across 25 warmed renders, p50 was **64.94 ms**, p95 **79.93 ms**, max **82.08 ms** (limit **1,500 ms**).
- Screenshot: [synthetic A4 case sheet](../screenshots/phase-07/case-sheet-synthetic.png), rendered and visually checked.

### 7.8 Loading, empty, error, and print states

- Queue and doctor views expose loading, empty, and retry states. Print CSS keeps the case sheet and footer visible while hiding navigation, editing controls, conflict dialog, and transcript.
- Verification: frontend **32 tests across 9 files**; typecheck, ESLint, locale parity (**316 keys per language**), contrast (**12 pairs**), and production build passed. Bundle: **479.04 kB** JavaScript (**149.44 kB gzip**).

### 7.9 Browser, access, audit, and performance results

- Full browser regression: **107 passed**. Focused Phase 7 browser suite: **5 passed**. Existing role/navigation tests cover all **18 role/page combinations**; doctor and nurse route restrictions pass.
- Two-browser doctor editing returns **409** for the stale edit and the merge/reapply flow succeeds. PDF download and browser print visibility pass. Nurse consent is displayed before identity fields; no identity request is sent until the consent action. Admin kiosk activation code appears once and disappears after dismissal.
- axe measured **0 serious/critical** findings each on doctor, nurse, and admin screens. The 5,000-row queue renders fewer than **50** rows; its urgent WebSocket update applied in **18.90 ms** (limit **<100 ms**).
- Backend command: `docker compose run --rm -T --no-deps -e WORKER_DATABASE_URL= -v /mnt/c/Users/lenovo/Downloads/OPD-Sathi:/workspace -w /workspace api python -m pytest services/api/tests --cov=services/api/app --cov-report=term-missing -q` — **340 passed**. Case-sheet reads, transcript reads, and PDF exports have explicit audit assertions; case-sheet and transcript content is not copied into audit metadata.
- Initial 7.9 coverage measurement, before step 7.10: combined `app/core` + `app/domain` **97%** (threshold **95%**, pass); backend overall **67%** (threshold **80%**, not yet met). Step 7.10 below records the final passing result; no coverage threshold was changed.
- Frontend commands: `npm --prefix apps/web test -- --reporter=dot` (**32 passed**), `npm --prefix apps/web run e2e -- --workers=1` (**107 passed**), `typecheck`, `lint`, `check-locales` (**316 keys/language**), `check-contrast` (**12 pairs**), and `build` (all pass).
- Screenshots: [doctor print view](../screenshots/phase-07/doctor-consultation-print.png), [nurse consent before identity](../screenshots/phase-07/nurse-consent-before-identity.png), [identity after consent](../screenshots/phase-07/nurse-identity-after-consent.png), [one-time kiosk code](../screenshots/phase-07/admin-kiosk-one-time-code.png), and [code dismissed](../screenshots/phase-07/admin-kiosk-code-dismissed.png).

### 7.10 Backend-wide coverage completion

- Added backend tests for admin, auth login, intake helper and route branches, queue operations, and encrypted repository behavior.
- Testing found two defects and both are fixed: selected intake danger tiles now reach the corresponding existing red-flag rules, and the staff queue returns department catalog entries instead of the last department's visit rows.
- The first hosted backend run revealed that repository tests relied on `APP_DATA_KEY` from the developer's local `.env`. They now use explicit deterministic synthetic encryption and HMAC keys, removing that environment dependency.
- Full backend command: `docker compose run --rm -T --no-deps -e WORKER_DATABASE_URL= -v /mnt/c/Users/lenovo/Downloads/OPD-Sathi:/workspace -w /workspace api python -m pytest services/api/tests --cov=services/api/app --cov-report=term-missing -q` — **366 passed**, 0 failed, 0 skipped; **80.0% (2,548/3,184 statements)** against the unchanged **80%** threshold. One Starlette/httpx deprecation warning.
- `ruff check services/api/app services/api/tests`, `mypy --strict services/api/app` (**51 source files, no issues**), and `bandit -q -r services/api/app` passed. `app/core` + `app/domain` remains **97%** against the **95%** threshold.

## Acceptance gate

All Phase 7 local gates pass, including backend-wide coverage at **80.0%**, browser regression (**107 passed**), focused Phase 7 E2E (**5 passed**), accessibility (**0 serious/critical findings** on the 3 staff screens), PDF (**1 A4 page**, **79.93 ms p95**), and large-queue update (**18.90 ms**, limit **100 ms**). Backend, frontend, and security CI passed on `14bc2df`; PR #5 merged into `main` as `16d4952`. The synthetic manual verification steps are in [the Phase 7 plan](../phase-plans/phase-07.md).

## Manual verification

Use only the synthetic seeded hospital. Sign in as a doctor, open a queued visit, edit a history field, export its PDF, and confirm the footer and one-page layout. Open the same visit in a second browser, save an edit in the first, then verify the second sees a conflict and can reapply its edit. Sign in as a nurse and verify consent appears before identity entry; review an alert and use an override only with a reason. Sign in as admin, create a synthetic kiosk, copy the activation code, dismiss it, and confirm it no longer appears. Never enter real patient information.

## Next phase preview

Phase 8 adds speech-to-text adapters, beginning with the current provider docs and ADR 003, then mock/provider fallbacks, bounded audio transport, microphone permission and voice activity states, transcript editing, and multilingual voice tests. Phase 8 is underway on `phase-08-voice`.
