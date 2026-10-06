# Phase 7 — Doctor and nurse consoles, PDF

**Status:** Active on `phase-07-consoles` from merged `main` (Phase 6 merged as PR #4). Steps 7.1–7.6 are implemented on draft PR #5. Step 7.1 uses role-filtered links and guarded routes, with tests for all 18 role/page combinations. All data and accounts used in automated and manual checks must be synthetic.

## Work order

1. [x] **7.1 Staff layout and role navigation:** real links and route guards for admin, doctor, and nurse pages. Tests cover every role/page combination.
2. [x] **7.2 Queue board:** department tabs, status filters, urgent pinning, virtual rows, pagination, sequence-aware live updates, connection state, and alert sound preference.
3. [x] **7.3 Case-sheet API:** audited reads, schema-validated JSON Patch updates guarded by `If-Match`, doctor source metadata, conflict response, and audit diff.
4. [x] **7.4 Doctor consult view:** reading-order case sheet, confidence/source details, edit and conflict flows, audited transcript view, consult actions, and keyboard controls.
5. [x] **7.5 Nurse console:** assisted registration, alert review, queue overrides, and no-show flow.
6. [x] **7.6 Admin screens:** user, kiosk, and department management with activation secrets shown only once.
7. [x] **7.7 PDF:** A4 multilingual case sheet through WeasyPrint, with the required history-only disclaimer; audit access and validate extracted text.
8. [x] **7.8 UI states:** loading, empty, error, and print states.
9. [ ] **7.9 Browser/access gates:** doctor, nurse, and admin flows; role access matrix; two-browser edit conflict; PDF content; axe and large-queue responsiveness.

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

## Acceptance gate

Follow Phase 7 in `AGENT_PLAYBOOK.md`. Measure the full role/access matrix, 409 edit conflict and merge dialog, PDF p95 ≤1.5 s and required text, rendering a 5,000-row queue with an update applied in <100 ms, audit entries for every case-sheet and transcript read, axe with zero serious/critical findings, and the applicable coverage thresholds. Record commands, counts, results, screenshots, and known limits in `docs/phase-reports/phase-07.md` before the phase is considered complete.

## Human verification

Use the synthetic queue. Sign in as a doctor, open a consult, change one field, and export the PDF. In a second browser, edit the same version and check that the first browser sees a conflict. Sign in as a nurse to record assisted registration and acknowledge an alert. Sign in as admin to create a synthetic kiosk and verify its activation code appears once. Never use real patient information.
