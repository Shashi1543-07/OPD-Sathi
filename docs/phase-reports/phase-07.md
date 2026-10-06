# Phase 7 progress report

**Status:** In progress on `phase-07-consoles`. Phase 6 is integrated into `main` as merge commit `0c833c3`; Phase 7 is tracked by draft PR #5. Steps 7.1–7.4 are implemented and required CI is green; the full Phase 7 acceptance gate is not complete.

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

## Remaining Phase 7 work

Steps 7.5–7.9 remain: nurse console; admin screens; multilingual PDF; UI states and print styling; and full browser/access/performance gates. The 5,000-row render/update-under-100-ms acceptance measurement, full audit assertions, axe run, human verification, and screenshots are still pending.
