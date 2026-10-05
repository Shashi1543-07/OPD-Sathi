# Phase 02 — Identity, audit, and rate limiting

**Status: Complete and merged.** PR #1 merged `phase-02-identity-security` into `phase-01-ui-foundation` as merge commit `900d0ef`; all six GitHub checks passed. The final CI evidence is recorded in the [Phase 2 report](../phase-reports/phase-02.md). Phase 3 remains gated on explicit human approval.

## Goal

Add secure, auditable staff and kiosk identity, isolate tenant data in PostgreSQL, enforce route authorization, and protect public endpoints from abuse. Follow Phase 2 in `AGENT_PLAYBOOK.md` (steps 2.1–2.10); patient session token support is limited to the authentication dependency until the interview phase uses it.

## Scope

- PostgreSQL tables and forced RLS for hospitals and the new tenant-owned identity/audit tables.
- Argon2id password hashing and fixed-algorithm, `kid`-identified JWTs with the Phase 2 claim set, 30-second clock skew, and 10-minute staff access tokens.
- Staff login, cookie-backed refresh rotation/reuse revocation, logout, lockout, role checks, audit records, one-time kiosk activation, and an operator CLI.
- Redis sliding-window rate limiting and specified Redis outage behavior.
- Typed frontend auth, protected staff routes, role-aware navigation, session expiry, and login/logout UX.
- Strict security headers, microphone policy, request body limits, and comprehensive tests.

No patient clinical data, diagnosis, treatment advice, or production identity provider is introduced in this phase. All seeded users and kiosk material are synthetic/local.

## Slices

### A — Database and security primitives (2.1–2.3) — complete

1. Add reversible identity/audit migrations with tenant keys, indexes, grants, append-only audit privileges, and forced RLS policies.
2. Add config validation for JWT lifetime, signing key and key id; implement Argon2id and strict JWT issue/verify primitives.
3. Add request transaction helpers that set transaction-local hospital scope and integration tests proving isolation between two hospitals and zero rows without scope.

**Verified:** migrations `0002_identity_security` and `0003_secret_lookup_rls` applied to local Postgres; all five current tenant tables have `FORCE ROW LEVEL SECURITY`; `osa_app` lacks audit update privilege; queries without scope return zero hospitals; PostgreSQL RLS/secret-lookup integration test passes. Argon2id/JWT tests pass and `core/security.py` coverage is 98%.

### B — Sessions, permissions, and audit (2.4–2.5, 2.7–2.8)

1. Implement staff login, five-failure/15-minute lockout, one-time refresh rotation with family revocation on reuse, and logout.
2. Implement deny-by-default auth dependencies and a route inventory with one explicit public-route allow-list.
3. Add one-time kiosk provisioning/activation, actor/request audit helper, and administrator-only cursor-paginated audit endpoint.

**Verified:** PostgreSQL lifecycle integration tests cover successful and failed login, five-failure lockout, refresh rotation/reuse family revocation, logout, wrong/reused kiosk activation, audit events, and cursor pagination. The route matrix covers all registered routes and the public allow-list.

### C — Abuse and transport protections (2.6, 2.10)

1. Add a Redis Lua sliding-window limiter, standard rate-limit headers and 429 problem responses.
2. Make login throttling fail closed with 503 when Redis is unavailable; use the in-process limiter for other configured limits during Redis outages.
3. Add CSP, HSTS in production, microphone-only-self permissions policy, and streaming request-size enforcement; cover headers and limits with tests.

**Verified:** The real Redis Lua test and API tests throttle request 11 with 429 headers; login fails closed with 503 when Redis is down, while other limited requests use the in-process fallback. Header and body-size tests pass. JWT and limiter modules each measure 100% unit-test coverage.

### D — Staff web integration and phase gate (2.9)

1. Add a typed API client with in-memory access token storage and single-flight refresh; never put tokens in localStorage.
2. Protect staff routes, render role-aware navigation and session-expired states, and connect the login/logout screen to the API.
3. Add Playwright success, generic wrong-password, lockout, and logout cases; complete the phase report and manual verification guide.

**Verified:** 16 web unit tests and 4 Phase 2 Playwright cases pass, including keyboard-only use and a zero-violation axe scan on staff sign-in. TypeScript, ESLint, locale parity, and the production build pass.

## Security design decisions

- Use PyJWT and `argon2-cffi`, as named in the locked stack; permit only the configured signing algorithm and validate `kid`, issuer, audience, required claims, expiry, and token purpose.
- Keep signing secrets in environment configuration. Never log passwords, refresh tokens, access tokens, or activation codes.
- Store only SHA-256 digests for opaque refresh and kiosk activation secrets. Put refresh tokens in Secure, HttpOnly, SameSite=Strict cookies (Secure is mandatory outside local development).
- Scope each tenant query within a transaction using PostgreSQL `set_config('app.hospital_id', ..., true)`. Do not rely on a caller-provided hospital id without authorization; authenticated routes derive scope from the verified token.
- Use the migration/operator role only for schema and explicit bootstrap CLI operations. Runtime API connections use `osa_app`, which has no RLS bypass.
- All newly exposed API routes must be in the security route inventory. Health/readiness and login/refresh/logout/kiosk activation are public by explicit declaration; everything else requires an appropriate identity.

## Verification and acceptance

- Reversible Alembic upgrade/downgrade/upgrade in CI; runtime-role update/delete of audit rows fails with PostgreSQL permission denied.
- Two-hospital RLS tests demonstrate no cross-tenant reads or writes and no rows without a transaction-local scope.
- JWT tests cover valid, expired, wrong key, `alg: none`, wrong audience, tampered claims, and wrong role. Security and limiter modules target at least 95% coverage.
- Login tests cover success, generic failure, lockout, refresh rotation/reuse, logout, kiosk wrong/reused activation, and expected audit records.
- Every non-public route returns 401 without credentials and 403 for an authenticated wrong role.
- Rate limiter tests prove request 11 is throttled, headers and Retry-After are correct, and Redis-down behavior matches the playbook.
- Frontend login/wrong password/lockout/logout tests, security header/body-size checks, Bandit with zero high findings, and the full repository CI pass.

## Implementation notes

- Keep the locked dependency strategy: update `requirements.in`, regenerate the hashed Python lock with uv for Python 3.12, then run `pip-audit` and CI.
- Use PostgreSQL-backed integration tests from the existing Compose/CI services; SQLite is not an acceptable substitute for RLS assertions.
- Keep clinical and queue APIs out of this phase; later phases can attach route policies as those APIs are introduced.
- Manual gate: run the local admin bootstrap, sign in, deliberately fail login six times, exercise the rate limit, activate a demo kiosk once, and inspect the immutable audit stream.
