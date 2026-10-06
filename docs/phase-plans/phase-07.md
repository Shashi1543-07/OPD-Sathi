# Phase 7 — Doctor and nurse consoles, PDF

**Status:** Active on `phase-07-consoles` from merged `main` (Phase 6 merged as PR #4). Steps 7.1–7.4 are implemented on draft PR #5; Step 7.5 is next. Step 7.1 uses role-filtered links and guarded routes, with tests for all 18 role/page combinations. All data and accounts used in automated and manual checks must be synthetic.

## Work order

1. [x] **7.1 Staff layout and role navigation:** real links and route guards for admin, doctor, and nurse pages. Tests cover every role/page combination.
2. [x] **7.2 Queue board:** department tabs, status filters, urgent pinning, virtual rows, pagination, sequence-aware live updates, connection state, and alert sound preference.
3. [x] **7.3 Case-sheet API:** audited reads, schema-validated JSON Patch updates guarded by `If-Match`, doctor source metadata, conflict response, and audit diff.
4. [x] **7.4 Doctor consult view:** reading-order case sheet, confidence/source details, edit and conflict flows, audited transcript view, consult actions, and keyboard controls.
5. [ ] **7.5 Nurse console:** assisted registration, alert review, queue overrides, and no-show flow.
6. [ ] **7.6 Admin screens:** user, kiosk, and department management with activation secrets shown only once.
7. [ ] **7.7 PDF:** A4 multilingual case sheet through WeasyPrint, with the required history-only disclaimer; audit access and validate extracted text.
8. [ ] **7.8 UI states:** loading, empty, error, and print states.
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

## Acceptance gate

Follow Phase 7 in `AGENT_PLAYBOOK.md`. Measure the full role/access matrix, 409 edit conflict and merge dialog, PDF p95 ≤1.5 s and required text, rendering a 5,000-row queue with an update applied in <100 ms, audit entries for every case-sheet and transcript read, axe with zero serious/critical findings, and the applicable coverage thresholds. Record commands, counts, results, screenshots, and known limits in `docs/phase-reports/phase-07.md` before the phase is considered complete.

## Human verification

Use the synthetic queue. Sign in as a doctor, open a consult, change one field, and export the PDF. In a second browser, edit the same version and check that the first browser sees a conflict. Sign in as a nurse to record assisted registration and acknowledge an alert. Sign in as admin to create a synthetic kiosk and verify its activation code appears once. Never use real patient information.
