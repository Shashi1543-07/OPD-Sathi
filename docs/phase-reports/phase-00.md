# Phase 00 — Foundations and tooling (5 October 2026, `phase-00-foundations`, uncommitted)

**Status: Incomplete — local code checks are green, but the phase gate is blocked on the host setup and CI.**

## 1. In plain words

The project now has its first API and web app skeleton, a locked set of dependencies, initial database migration, local service configuration, CI workflow, and setup documentation. The API can answer health checks without its dependencies, and it reports not-ready when Postgres or Redis are unavailable. The host does not currently have Docker available, so the full local stack and end-to-end Phase 0 gate could not be run.

## 2. What was built

- **0.1 — Environment recorded:** Windows host, globally available Node 20.19.5/npm 10.8.2 and Python 3.11.9, no Docker on PATH, and WSL distribution enumeration denied. Project-local Python 3.12.15 and official Node 24.21.0 were provisioned under ignored `.uvtooling/` files. Node archive SHA-256 was checked against the official checksum list.
- **0.2 — Repository and settings:** C4 layout, `.gitignore`, `.editorconfig`, `.env.example`, and canonical `AGENT_PLAYBOOK.md` created. Gitleaks 8.30.1 scanned 35 source/configuration paths with **zero findings**.
- **0.3 — Compose:** Postgres 16.9, Redis 7.4.2, API, and worker services are configured with health checks, password requirements, pinned tags, and non-root API container. `docker compose up` health validation was not possible because Docker is absent.
- **0.4 — API:** FastAPI app factory, typed settings, JSON logging, request ID, problem+json errors, security headers, CORS, `/healthz`, and `/readyz` implemented. **5 API tests passed**; live `/healthz` and `/docs` returned 200. Live `/readyz` returned 503 while both backing services were unavailable.
- **0.5 — Database:** Async SQLAlchemy engine, Alembic, initial `hospitals` migration, app/migration DB roles and a separate migration connection configured. The migration successfully generated offline SQL. The actual upgrade/downgrade/upgrade cycle remains unrun without Postgres.
- **0.6 — Web:** Strict React/TypeScript/Vite shell, placeholder routes, three locale files, PWA shell, ESLint, Prettier, and Vitest smoke test implemented. **1 frontend test passed; build, lint, typecheck, and locale parity checks passed.**
- **0.7 — Types:** OpenAPI export and `openapi-typescript` generation work; CI includes a stale-type check. CI has not run.
- **0.8 — Makefile:** All **34 documented targets are declared**. They could not be exercised because GNU Make is not installed/available on this Windows host.
- **0.9 — CI:** GitHub Actions workflow created for Python, Postgres/Redis, frontend, migrations, secret scanning, and security checks. YAML parses successfully; the remote workflow has not run.
- **0.10 — Docs:** README, ADR 001/002, safety/privacy/evaluation/scaling/demo stubs, problem statement draft, checklist, and phase report created.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---|---|---|
| CI | Green | Not run; no remote workflow run | Blocked |
| `make test` | Pass | Equivalent direct runs: API **5/5**, web **1/1** | Pass locally |
| Fresh clone reaches running app | ≤10 minutes | Not measured; Docker/Make missing | Blocked |
| Secrets | 0 findings | Gitleaks **0 findings / 35 paths** | Pass |
| `/readyz` degradation | 503 names failing dependency | Unit test checks both failing dependencies; live API returned 503 with Postgres and Redis unavailable | Pass for unit behavior; Compose path unverified |

The Phase 0 gate is **not met** because CI, Compose health, migration cycle, Make targets, and the fresh-clone run have not been verified.

## 4. Commands run

- Python 3.12.15: `python -m pytest -p no:cacheprovider services/api/tests --cov=services/api/app --cov-report=term` — **5 passed**.
- `ruff check services/api/app services/api/tests services/api/scripts services/api/migrations` — passed.
- `mypy --strict services/api/app` — passed, 22 source files.
- `bandit -q -r services/api/app` — passed, no findings.
- `pip-audit -r services/api/requirements.lock.txt` — **no known vulnerabilities**.
- `pip-audit -r services/api/requirements-security.lock.txt` — **no known vulnerabilities** in the Semgrep CI lock.
- Node 24.21.0/npm 11.19.0: `npm test -- --reporter=dot` — **1 passed**; `npm run build` — passed, initial JS **98.39 KB gzipped**; `npm run lint` and `npm run typecheck` — passed; `npm run check-locales` — **3 languages, 3 keys each**; `npm audit --audit-level=high` — **0 vulnerabilities**.
- `alembic -c services/api/alembic.ini upgrade --sql head` — generated initial migration SQL successfully. Database migration cycle not run.
- Python YAML parse of `docker-compose.yml` and `.github/workflows/ci.yml` — passed.
- Gitleaks 8.30.1 directory scans — **0 findings across 35 project paths**.
- Live Uvicorn smoke check: `/healthz` 200, `/docs` 200, `/readyz` 503 with dependencies absent.
- `make` and `docker compose` were not available, so their commands could not be run.

## 5. Screenshots and artifacts

No screenshots yet. Frontend build output is generated under ignored `apps/web/dist/`.

## 6. How the human tests this phase

After Docker Desktop is installed and a WSL2 Linux distribution is available:

1. Copy `.env.example` to `.env` and replace local placeholders with development-only secrets.
2. From WSL2, create a Python 3.12 virtual environment, install the locked API requirements, and run `npm ci --prefix apps/web` using Node 24 LTS.
3. Run `make up`, then apply the schema with `make migrate`.
4. Run `make api` and `make web` in separate terminals.
5. Open `http://localhost:8000/docs`, `/healthz`, `/readyz`, and `http://localhost:5173`; all should report healthy while Postgres and Redis are running.
6. Stop Redis and verify `/readyz` returns 503 naming `redis`; start it again and verify `/readyz` returns 200.
7. Run the migration upgrade/downgrade/upgrade cycle and `make test`.

## 7. Known issues and decisions

- Docker Desktop is not installed or on PATH. `wsl --status`/distribution enumeration returned `E_ACCESSDENIED`; WSL2 is installed but no accessible distribution was found.
- GNU Make is unavailable in the host PowerShell session; use WSL2 for the playbook's supported command path.
- This workspace's `.git` directory is read-only under the active filesystem permissions. `git init --initial-branch=phase-00-foundations` succeeded, but Git could not create `.git/index.lock`, so nothing was staged or committed.
- Python 3.12, Node 24, Gitleaks, the virtual environment, and npm dependencies are local ignored downloads/installations; they are not source-controlled artifacts.
- The official SIH wording in `docs/PROBLEM_STATEMENT.md` remains to be confirmed before submission.

## 8. Next phase preview

Phase 1 builds the patient-facing visual foundation, multilingual language selection, accessible shared controls, and visual checks. Do not start Phase 1 until the Phase 0 environment and gate are complete and the human approves Phase 0.

**Next action:** make an accessible WSL2 Linux distribution available and install Docker Desktop with WSL integration; then rerun the Compose, migration, Make, and CI checks to finish Phase 0.
