# Phase 3 report: data model, seeds, and pagination

**Status: Implementation complete locally on `phase-03-data-model`; GitHub CI and merge are pending.**

## Delivered

- Added `departments`, `patients`, `consents`, `visits`, `case_sheets`, `interview_turns`, `red_flag_events`, `outbox`, `notifications`, and `documents`, with tenant-aware foreign keys, constraints, indexes, and forced RLS.
- Added authenticated AES-256-GCM encryption (`v1:` ciphertext) and HMAC lookup digests for patient identifiers. Ciphertext is bound to its table, column, and row context.
- Added strict case-sheet validation, checked-in JSON Schema, and a schema drift check in CI.
- Added tenant-scoped async repositories, signed keyset cursors, Redis idempotency, and audited/paginated admin department, user, and kiosk endpoints.
- Added fictional Nagpur demo provisioning and deterministic synthetic bulk visit generation.
- Added a queue query plan artifact at [phase-03-query-plan.json](phase-03-query-plan.json).

## Verification evidence

| Gate | Result |
|---|---|
| Backend suite and `app/core` + `app/domain` coverage | **Pass:** 90 tests passed; 98% combined coverage (430/437 statements). One Starlette/httpx deprecation warning. |
| Encryption | **Pass:** round-trip, tamper/context rejection, key validation, keyed digest behavior, and 100,000 unique nonce checks. |
| Database isolation and confidentiality | **Pass:** PostgreSQL integration tests confirmed forced RLS on all ten new tenant tables, no unscoped rows visible to the app role, and no plaintext patient fields in stored values. |
| Concurrent keyset pagination | **Pass:** 10,000 initial rows scanned while 100 later rows were inserted; no duplicate or missing IDs. |
| Admin writes | **Pass:** PostgreSQL/Redis integration check covered creation, response replay, changed-body rejection, audited listing, and patch. |
| Migration reversibility | **Pass:** on a disposable database, upgrade to head, downgrade to base, then upgrade to head. |
| Queue benchmark | **Pass:** 100,000 visits; PostgreSQL used `ix_visits_queue`; 100 measured requests had 1.99 ms p95 and 0.075 ms plan execution. |
| Static checks | **Pass locally:** Ruff, strict mypy, Bandit, and checked-in case-sheet schema. |
| Frontend checks | **Pass locally:** lint, typecheck, 16 Vitest tests, and production build; OpenAPI client types regenerated. |
| GitHub Actions and dependency audit | **Pending:** run on the pushed branch. A local `pip-audit` attempt did not return from its online vulnerability service in time to establish a result. |

The frontend build produced a 121.37 KB gzip JavaScript bundle. PostgreSQL timing is from the local development database and is a reproducible development measurement, not a production capacity claim.

## Manual test instructions

Follow the [Phase 3 manual demo steps](../phase-plans/phase-03.md#manual-demo-steps). Configure private, persistent encryption keys before creating data. The seed writes only fictional content; first-run passwords and kiosk activation codes are printed once, so store the terminal output securely. The full 100,000-visit seed is optional for the normal demo and can be run with `make seed-bulk`.

## Remaining handoff

At the time this report was written, Phase 4 had not started. Phase 4 implementation has since been added locally; see its [plan](../phase-plans/phase-04.md) and [report](phase-04.md). This update does not change the pending Phase 3 remote CI/merge status recorded above.
