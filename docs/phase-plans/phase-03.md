# Phase 3 plan: data model, seeds, and pagination

**Status: Implementation complete locally on `phase-03-data-model`; GitHub CI and merge were pending at this handoff.** Phase 3 follows the [build playbook](../../AGENT_PLAYBOOK.md#phase-3--data-model-seeds-and-pagination-f5-schema). Phase 4 implementation has since been added; see the [Phase 4 plan](phase-04.md) and [report](../phase-reports/phase-04.md).

## Scope delivered

- Add the ten tenant-scoped domain tables, indexes, constraints, composite foreign keys, forced row-level security, and reversible Alembic migration.
- Encrypt patient identifiers with AES-256-GCM and bind ciphertext to row/column context; use keyed HMAC digests for lookup.
- Define a strict Pydantic case-sheet contract and check its generated JSON Schema in CI.
- Add tenant-scoped async repositories, signed `(created_at, id)` keyset pagination, and Redis idempotency for admin writes.
- Add audited and paginated admin list/create/patch endpoints for departments, users, and kiosks.
- Seed fictional Demo Government Hospital, Nagpur data and deterministic synthetic bulk visits.
- Measure and save the queue query plan with 100,000 visits.

## Local verification gates

The completion evidence and measured results are in the [Phase 3 report](../phase-reports/phase-03.md). GitHub Actions remains the final check on the pushed branch.

## Manual demo steps

1. Use a private `.env`. Set persistent base64-encoded 32-byte `APP_DATA_KEY` and `APP_HMAC_KEY` values and `DEMO_ADMIN_PASSWORD` / `DEMO_STAFF_PASSWORD` values with at least 12 characters. Keep the encryption keys stable across restarts.
2. Start PostgreSQL and Redis, then run `make migrate`.
3. Run `make seed`. Review the fictional hospital ID and newly created administrator/staff credentials printed by the CLI. Store them privately. Newly created kiosk activation codes are one-time output; store them securely.
4. Start the API and web app and sign in to the staff app with the demo hospital ID and administrator credentials.
5. Run `make seed-bulk` to add 100,000 deterministic synthetic visits. Use `python services/api/scripts/benchmark_queue.py` to repeat the local queue measurement.
6. Confirm `python -m app.cli seed-demo` is safe to rerun. Existing user passwords and kiosk activation codes are not reprinted or rotated.

Use synthetic data only. Do not use this seed or these credentials with real patient data or a live hospital system.

## Handoff

Review the GitHub workflow checks for the Phase 3 branch. Phase 4 work is now present locally and its evidence is recorded in the Phase 4 report; Phase 4 approval is still required before Phase 5.
