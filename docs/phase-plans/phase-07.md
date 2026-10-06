# Phase 7 — Doctor and nurse consoles, PDF

**Status:** Active on `phase-07-consoles`, started after the user's explicit instruction to continue from Phase 6. Step 7.1 is complete: staff navigation now uses role-filtered links and guarded routes. Its role/page matrix tests pass for all 18 combinations. All data and accounts used in automated and manual checks must be synthetic.

## Work order

1. [x] **7.1 Staff layout and role navigation:** real links and route guards for admin, doctor, and nurse pages. Tests cover every role/page combination.
2. [ ] **7.2 Queue board:** department selection, status filters, urgent pinning, virtual rows, pagination, sequence-aware live updates, connection state, and alert sound preference.
3. [ ] **7.3 Case-sheet API:** audited reads, schema-validated JSON Patch updates guarded by `If-Match`, doctor source metadata, conflict response, and audit diff.
4. [ ] **7.4 Doctor consult view:** reading-order case sheet, confidence/source details, edit and conflict flows, audited transcript view, consult actions, and keyboard controls.
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

## Acceptance gate

Follow Phase 7 in `AGENT_PLAYBOOK.md`. Measure the full role/access matrix, 409 edit conflict and merge dialog, PDF p95 ≤1.5 s and required text, rendering a 5,000-row queue with an update applied in <100 ms, audit entries for every case-sheet and transcript read, axe with zero serious/critical findings, and the applicable coverage thresholds. Record commands, counts, results, screenshots, and known limits in `docs/phase-reports/phase-07.md` before the phase is considered complete.

## Human verification

Use the synthetic queue. Sign in as a doctor, open a consult, change one field, and export the PDF. In a second browser, edit the same version and check that the first browser sees a conflict. Sign in as a nurse to record assisted registration and acknowledge an alert. Sign in as admin to create a synthetic kiosk and verify its activation code appears once. Never use real patient information.
