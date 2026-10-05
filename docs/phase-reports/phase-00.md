# Phase 00 — Foundations and tooling

**Updated:** 5 October 2026

**Branch/commit:** `phase-00-foundations` / `b5cd827` (pushed; local branch matched origin before this report update)

**Status: Local acceptance checks pass; final Phase 0 gate awaits GitHub Actions confirmation for `b5cd827`.**

## What exists

- Repository layout, settings, `.gitignore`, `.editorconfig`, placeholder `.env.example`, README, decisions, and phase documentation.
- Docker Compose configuration for Postgres, Redis, API, and worker, with pinned images and health checks.
- FastAPI foundation with configuration, structured logging, request IDs, problem responses, security headers, CORS, `/healthz`, and `/readyz`.
- Async SQLAlchemy, Alembic, initial hospitals migration, and separate app/migration database roles.
- React, TypeScript, Vite, locale seed files, PWA shell, OpenAPI type generation, and frontend quality tooling.
- A Makefile with 34 documented targets and GitHub Actions jobs for backend, frontend, and security.
- Ubuntu WSL 2 with Docker Desktop 4.93.0, Engine 29.8.1, Compose 5.5.1, Python 3.12.15, Node 24.21.0, npm 11.19.0, and GNU Make.
- A local `.env` with random development credentials, confirmed ignored by Git. Never share or commit it.
- Commit `b5cd827` conditions `pywin32` on Windows after the original security job failed to install it on Linux.

## Verification completed

- `make lint`: Ruff, strict mypy, ESLint, and TypeScript checks passed.
- `make test`: API 5/5 and web 1/1 passed, in both the workspace and a clean checkout.
- Frontend production build passed; locale parity passed for English, Hindi, and Marathi; `npm audit --audit-level=high` reported 0 vulnerabilities.
- Security checks previously recorded: Bandit and both `pip-audit` checks passed; Gitleaks reported 0 findings across 35 project paths.
- Compose: Postgres, Redis, API, and worker started; Postgres and Redis became healthy and the API health check passed.
- Migration cycle: upgrade → downgrade → upgrade completed successfully.
- Live API checks: `/healthz` and `/readyz` returned 200. While Redis was stopped, `/readyz` returned 503 and named Redis; after Redis was restarted it returned ready again.
- Clean checkout of `phase-00-foundations`: Python and web locks installed, Compose started, migration ran, and `make test` passed. Elapsed setup was 31 seconds with Python/npm/Docker prerequisites installed and package/image caches warm. This is not a cold-machine provisioning measurement.
- The supplied screenshot showed the old CI run failing on `pywin32==311`; the fix is included in `b5cd827`. GitHub Actions for the fixed commit could not be retrieved from this environment, so its green status remains unverified.

## Gate status

| Gate | Required | Evidence | Status |
|---|---|---|---|
| CI | Green for `b5cd827` | Follow-up Actions run is not accessible from this environment | Pending |
| `make test` | Pass | API 5/5; web 1/1, including clean checkout | Pass |
| Fresh-checkout setup | ≤10 minutes | 31 seconds with prereqs installed and caches warm | Pass |
| Secrets | 0 findings | Gitleaks: 0 findings / 35 paths; `.env` ignored | Pass |
| Readiness degradation | Redis failure reports 503; recovers when Redis returns | Verified live | Pass |
| Compose and migrations | Services healthy; upgrade/downgrade/upgrade succeeds | Verified live | Pass |

**The Phase 0 gate is not yet marked complete because CI must be green.** Please open the GitHub Actions run for commit `b5cd827` and confirm it succeeds. If it fails, share the failing job/log so it can be fixed.

## Phase 1 preview

Phase 1 establishes the reusable visual system, multilingual language selection, accessible shared components, kiosk and staff shells, static fake-data previews, and visual/accessibility checks. The plan is recorded in [phase-01.md](../phase-plans/phase-01.md). Implementation remains paused until the Phase 0 gate passes and the phase approval is confirmed.
