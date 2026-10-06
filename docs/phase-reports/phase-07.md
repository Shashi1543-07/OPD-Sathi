# Phase 7 progress report

**Status:** In progress on `phase-07-consoles`. Phase 6 is integrated into `main` as merge commit `0c833c3`; Phase 7 is tracked by draft PR #5. The full Phase 7 acceptance gate is not complete.

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

## Verification recorded

- Frontend tests: **23 passed** across **6** test files.
- Queue access policy tests: **3 passed**.
- Typecheck, ESLint, production build, Ruff, strict mypy, locale parity (**219 keys per language**), and contrast checks (**12 pairs**) passed.
- Production bundle: **441.03 kB** JavaScript (**139.29 kB gzip**); build completed successfully.
- `git diff --check` passed. Hosted PR #5 CI will run against the committed branch head.

The complete local backend suite was also attempted: **318 passed**, one Redis websocket latency integration failed to subscribe in this environment, and three tests could not create their temporary directories under the default Windows pytest temp root. Those environment-related failures need the hosted CI result before the phase can be considered complete.

## Remaining Phase 7 work

Steps 7.3–7.9 remain: version-guarded audited case-sheet API updates; doctor consult view and conflict flow; nurse console; admin screens; multilingual PDF; UI states and print styling; and full browser/access/performance gates. The 5,000-row render/update-under-100-ms acceptance measurement, full audit assertions, axe run, human verification, and screenshots are still pending.
