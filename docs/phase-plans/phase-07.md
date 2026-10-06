# Phase 7 — Doctor and nurse consoles, PDF

**Status:** Phase 7 is complete and merged to `main` as `16d4952` after backend, frontend, and security CI passed. Backend-wide coverage is **80.0% (2,548/3,184 statements)**, meeting the playbook's 80% target. All data and accounts used in automated and manual checks are synthetic.

## Work order

1. [x] **7.1 Staff layout and role navigation:** real links and route guards for admin, doctor, and nurse pages. Tests cover every role/page combination.
2. [x] **7.2 Queue board:** department tabs, status filters, urgent pinning, virtual rows, pagination, sequence-aware live updates, connection state, and alert sound preference.
3. [x] **7.3 Case-sheet API:** audited reads, schema-validated JSON Patch updates guarded by `If-Match`, doctor source metadata, conflict response, and audit diff.
4. [x] **7.4 Doctor consult view:** reading-order case sheet, confidence/source details, edit and conflict flows, audited transcript view, consult actions, and keyboard controls.
5. [x] **7.5 Nurse console:** assisted registration, alert review, queue overrides, and no-show flow.
6. [x] **7.6 Admin screens:** user, kiosk, and department management with activation secrets shown only once.
7. [x] **7.7 PDF:** A4 multilingual case sheet through WeasyPrint, with the required history-only disclaimer; audit access and validate extracted text.
8. [x] **7.8 UI states:** loading, empty, error, and print states.
9. [x] **7.9 Browser/access gates:** doctor, nurse, and admin flows; role access matrix; two-browser edit conflict; PDF content; axe and large-queue responsiveness.
10. [x] **7.10 Backend-wide coverage gate:** add tests for uncovered auth, intake, queue, and repository behavior; fix defects revealed by those tests; rerun the full backend suite at the unchanged 80% threshold.

### Step 7.1 verification

- `npm --prefix apps/web test -- --reporter=dot`: **18 passed**.
- `npm --prefix apps/web run typecheck`: passed.
- `npm --prefix apps/web run lint`: passed with no warnings.
- `npm --prefix apps/web run check-locales`: **219 keys per language** across English, Hindi, and Marathi.
- `npm --prefix apps/web run build`: passed; JavaScript bundle **409.82 kB** (**129.67 kB gzip**).

### Step 7.2 verification

- `npm --prefix apps/web test -- --reporter=dot`: **23 passed** (including department/status filters, load-more paging, urgent pin order, sound preference persistence, and sequence-gap recovery).
- `npm --prefix apps/web run typecheck`, `npm --prefix apps/web run lint`, and `npm --prefix apps/web run build`: passed.
- `npm --prefix apps/web run check-locales`: **219 keys per language**; `npm --prefix apps/web run check-contrast`: **12 documented pairs passed**.
- `.venv/Scripts/python.exe -m pytest services/api/tests/unit/test_queue_board.py -q`: **3 passed**; Ruff and strict mypy passed.
- The queue API constrains doctors and nurses to their assigned active department. Admins can switch among active departments. The list is paginated, virtualized, urgent-first, and updates from contiguous websocket sequence deltas; a sequence gap triggers a fresh page.
- The complete backend suite was attempted locally: **318 passed**, one existing Redis websocket latency integration failed to subscribe in this environment, and three tests could not create temporary directories under the default Windows pytest temp root. Required hosted CI for PR #5 is the authoritative cross-platform gate.

### Step 7.3 verification

- `services/api/tests/unit/test_case_sheet_patch.py` and `services/api/tests/unit/test_case_sheet_api.py`: **13 passed**.
- Case-sheet reads are limited to doctors/nurses in their assigned department and create an audit event. Writes are doctor-only, require a positive `If-Match` version, lock and compare the current row version, validate the complete `CaseSheet`, update doctor provenance, and audit changed paths/operations without copying clinical values into audit metadata.
- Conflict responses use **409**; missing version uses **428**. Reads and successful writes return the current strong ETag.
- Ruff, strict mypy, Bandit, and regenerated OpenAPI TypeScript types passed. Hosted PR #5 CI passed backend, frontend, and security at `fee4776`: [Actions run](https://github.com/Shashi1543-07/OPD-Sathi/actions/runs/37410094888).

### Step 7.4 progress

- Added the doctor-only, department-scoped transcript endpoint. Interview text is decrypted by the repository and each read is audited without copying transcript text into the audit record.
- Replaced the consultations placeholder with a case-sheet view in reading order, confidence/source metadata, inline edits, a 409 reload/reapply dialog, audited transcript panel, Start/Done actions, print shortcut, and N/E/P keyboard controls.
- Doctor consultation tests: **2 passed**, including versioned edits and conflict reapplication. The full frontend suite, typecheck, ESLint, locale parity, production build, E2E, Lighthouse, contrast, generated-type parity, backend, and security checks passed at `c990f49`: [Actions run](https://github.com/Shashi1543-07/OPD-Sathi/actions/runs/37411307425). Broader role-matrix and two-browser edit checks remain in Step 7.9.

### Step 7.5 verification

- Added nurse-only assisted registration. It uses the existing intake flow and requires the patient to review and accept the selected-language consent before name and demographic details can be entered. Consent is stored with channel `nurse`; audit entries identify the nurse without including patient details.
- The API verifies the nurse is active and has an active department assignment through `IntakeRepository`; session state and patient access remain visit- and hospital-scoped. Redis startup failure cancels the placeholder visit.
- Nurse queue access provides alert acknowledgement, routing confirmation, reasoned urgency override, and no-show actions through the existing department-scoped queue API.
- Verification: intake API unit tests **5 passed**; frontend **26 tests passed across 8 files**; Ruff, strict mypy, Bandit, TypeScript, ESLint, locale parity (**269 keys per language**), production build, and `git diff --check` passed.

### Step 7.6 verification

- Added the administrator screen for paginated department, staff-user, and kiosk lists; create forms; activation/deactivation controls; translated English, Hindi, and Marathi labels; request loading/error/empty states; and an in-memory-only kiosk activation code display.
- Kiosk creation returns its activation code in the initial response. Idempotency storage removes the one-time field before caching, so a retry with the same key cannot retrieve the secret again.
- Verification: `npm --prefix apps/web test -- --reporter=dot` — **27 passed across 9 files**; `npm --prefix apps/web run typecheck` and `lint` passed; `check-locales` reported **310 keys per language**. `.venv/Scripts/python.exe -m pytest services/api/tests/unit/test_domain_data.py -q` — **53 passed**; Ruff passed and strict mypy reported **50 source files, no issues**.
- The full Prettier check currently reports **77** style warnings across existing generated output and untouched files. The changed Phase 7.6 source files were formatted individually; unrelated files were left unchanged.

### Step 7.7 verification

- Added `GET /visits/{id}/case-sheet.pdf`, restricted to active doctor/nurse staff in the visit's assigned department. The PDF includes the queue token, attention reasons, multilingual history sections, and the required history-only disclaimer. Exports are audited with the case-sheet version and red-flag count; responses are marked `Cache-Control: no-store`.
- Added a doctor-console PDF download using the authenticated staff API request. The API image and CI install WeasyPrint's Pango libraries, Inter, Noto Sans Devanagari, and Poppler for PDF text checks.
- Verification on Ubuntu WSL with the locked Python 3.12 environment: PDF/API tests **7 passed**; `pdftotext` found the synthetic Hindi complaint, queue number, attention reason, and disclaimer; `pdfinfo` reported **1 A4 page (595.276 × 841.89 pt)**. After startup font warm-up, **25** synthetic renders measured **p50 64.94 ms, p95 79.93 ms, max 82.08 ms**, under the **1,500 ms p95** limit.
- Frontend: **28 tests passed across 9 files**, TypeScript, ESLint, locale parity (**313 keys per language**), and production build passed. Bundle: **477.93 kB** JavaScript (**149.15 kB gzip**). Ruff and strict mypy passed (**51 source files**); `git diff --check` passed.
- Screenshot: [synthetic A4 case sheet](../screenshots/phase-07/case-sheet-synthetic.png). PDF layout was rendered and visually inspected after the disclaimer fix.

### Step 7.8 verification

- Queue and doctor screens now expose distinct loading, empty, and error states to assistive technology. Queue rendering distinguishes a loading request, no active departments, and filters with no matching patients. Doctor loading and request errors include a retry action; opening consultations without a selected visit presents a return-to-queue prompt.
- Added an A4 browser print layout for the doctor case sheet. Print media shows the case sheet and its disclaimer, hides navigation, edit controls, conflict overlay, and transcript, and avoids splitting individual fields across pages.
- Verification: frontend **32 tests passed across 9 files**. Tests now exercise queue and doctor loading/empty/error states. TypeScript, ESLint, locale parity (**316 keys per language**), contrast checks (**12 pairs**), production build, and `git diff --check` passed. Production bundle: **478.99 kB** JavaScript (**149.43 kB gzip**).
- Browser print visibility and the broader doctor/nurse/admin/accessibility/performance flows are measured in Step 7.9.

### Step 7.9 verification

- Full frontend Playwright regression: **107 passed**. The 18 role/page combinations pass; doctor and nurse route restrictions, two-browser **409** conflict/reapply flow, PDF download, print visibility, nurse consent-before-identity, and admin kiosk secret dismissal pass.
- Focused Phase 7 Playwright run: **5 passed**. axe reported **0 serious/critical findings** on doctor, nurse, and admin screens. The virtualized queue rendered fewer than **50** rows from a **5,000-row** fixture; its urgent WebSocket update applied in **18.90 ms** (limit **<100 ms**).
- Backend container suite: **340 passed**. `case_sheet.read`, `transcript.read`, and PDF export audit assertions pass. PDF checks recorded above: **1 A4 page**, required synthetic content, p95 **79.93 ms** (limit **1,500 ms**).
- Initial 7.9 coverage measurement, before step 7.10: combined `app/core` + `app/domain` **97%** (limit **95%**); backend-wide **67%** (playbook limit **80%**). Step 7.10 below closes the coverage gap without changing the threshold.
- Frontend: **32 unit tests passed**; TypeScript, ESLint, locale parity (**316 keys per language**), contrast (**12 pairs**), and production build passed. Bundle: **479.04 kB** JavaScript (**149.44 kB gzip**).
- Browser evidence: [doctor print view](../screenshots/phase-07/doctor-consultation-print.png), [nurse consent before identity](../screenshots/phase-07/nurse-consent-before-identity.png), [nurse identity after consent](../screenshots/phase-07/nurse-identity-after-consent.png), [one-time kiosk code](../screenshots/phase-07/admin-kiosk-one-time-code.png), and [code dismissed](../screenshots/phase-07/admin-kiosk-code-dismissed.png). The PDF screenshot is [here](../screenshots/phase-07/case-sheet-synthetic.png).

### Step 7.10 verification

- Added focused backend tests for admin lifecycle/helper paths, successful and failed login, patient intake answer/correction/read/cancel flows, queue routing/status/alert flows, and encrypted patient/case-sheet repository behavior.
- The tests exposed and fixed two defects: intake danger-tile IDs are now mapped to the existing red-flag rule IDs before evaluation, and the staff queue keeps its department catalog separate from per-department visit rows.
- Hosted CI initially exposed an environment-dependent fixture: three repository tests expected `APP_DATA_KEY` from the developer's local `.env`. The tests now provide deterministic synthetic data and HMAC keys directly, so they pass in CI without project secrets.
- Full backend command: `docker compose run --rm -T --no-deps -e WORKER_DATABASE_URL= -v /mnt/c/Users/lenovo/Downloads/OPD-Sathi:/workspace -w /workspace api python -m pytest services/api/tests --cov=services/api/app --cov-report=term-missing -q` — **366 passed**, 0 failed, 0 skipped; **2,548/3,184 statements = 80.0%** (threshold **80%**, pass). One existing Starlette/httpx deprecation warning remains.
- `ruff check services/api/app services/api/tests`, `mypy --strict services/api/app` (**51 source files, no issues**), and `bandit -q -r services/api/app` all passed. The combined `app/core` + `app/domain` gate remains **97%** against **95%**.

## Acceptance gate

The Phase 7 role/access, conflict, PDF, queue, audit, axe, and coverage checks pass. Backend-wide coverage is **80.0% (2,548/3,184 statements)** against the **80%** target; combined `app/core` + `app/domain` coverage is **97%** against the **95%** target. Backend, frontend, and security CI passed, and PR #5 is merged to `main` as `16d4952`.

## Human verification

Use the synthetic queue. Sign in as a doctor, open a consult, change one field, and export the PDF. In a second browser, edit the same version and check that the first browser sees a conflict. Sign in as a nurse to record assisted registration and acknowledge an alert. Sign in as admin to create a synthetic kiosk and verify its activation code appears once. Never use real patient information.
