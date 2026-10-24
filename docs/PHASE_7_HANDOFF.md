# Phase 7 handoff: G3 nearly done, and exactly where it stopped

For whoever continues Phase 7 (`docs/PHASE_7_PRODUCTION_PLAN.md`). Updated on 24 Oct 2026, in
the middle of G3. Read this first, then the plan's section 4 for each task's "done when".

## State of `main`

| Gate | Pull request | What it delivered |
|---|---|---|
| G0 Fixed | #17 | Worker SMS without patient keys, SMS opt-in by the patient's own tick, settings wired, images pinned |
| G1 Secure build | #18 | Production overlay and HTTPS, staff account security and MFA, key rotation, least privilege, audit log screen, Trivy/ZAP/Schemathesis, threat model, ASVS L2 self-assessment |
| G2 Operable | #35 | Prometheus/Alertmanager with 19 tested alerts, pgBackRest backups with a restore drill on every change, 180-day logs, Settings → System, k6 load tests, signed releases, paper fallback pack, nightly real install |
| Dependencies | #36, #48 | Updates tested together; React 19 (#48) |
| Release | tag `v0.7.0` | First release, cut on `main` at #48; the release workflow ran green |

## G3 "Pilot-ready": branch `feat/phase-7-g3` (pushed; no pull request opened yet)

One pull request for the gate, merged when every check is green (the owner has agreed to
that). Every commit below has passed CI on push (backend, frontend, production-smoke with the
real-API browser suite, images, security). The nightly workflows (`nightly-failures.yml`,
`nightly-load.yml` with the new upgrade job) run only on the pull request: **they have never
run in CI yet**; expect to fix small things there.

### Done on the branch

| Task | What | Commit |
|---|---|---|
| 7.11.1 | Doctor's own notes, versioned, on the PDF | 5089683 |
| 7.11.2 | Review of systems, off by default | a854838 |
| 7.11.3 | Results over time from accepted readings | ab0b5b4 |
| 7.11.4 | Re-issue a lost OPD card | a4c82cf |
| 7.11.5 | AI-settings screen (staff editing and audit log existed from G1) | 6827602 |
| 7.11.7, 7.5.3 | Who is answering for the patient | 631ebc8 |
| 7.11.8, 7.11.9, 7.11.11, 7.8.4 (decision) | LIMITATIONS.md, RAG_DESIGN.md, ADRs 013–015 | cf1ed4a |
| 7.13.6 | Web coverage gate (ADR 016) | 9442a45, e0b3764 |
| 7.10.1 | FHIR R4 export of a visit (ADR 017) | 6af3ed8 |
| 7.14.7 | Per-hospital switches: SMS messages (off by default) and phone check-in (migration 0032) | 9a81b1d |
| 7.12.4 | Queue board fully translated (77 strings, en/hi/mr) | bd6321d |
| 7.7.8, 7.13.4 | Failure drills nightly (Redis, PostgreSQL, worker, disk full; 19 checks pass locally). Found and fixed: `RedisDown` could never fire (the worker crashed when Redis went away); `ResilientWorker` | 3ca4b01 |
| bug | Deadlock between session renewal and a password change (500, signed out); found by the new real-API suite; regression test reproduces it | 95bcf9a |
| 7.15.1, 7.15.2, 7.15.3 (part) | `docs/print` (staff cards ×3 roles ×3 languages, two patient signs, CI `--check`), DEPLOYMENT.md "check the installation", Windows Server → Hyper-V Linux VM | cc0b088 |
| 7.13.2 | End-to-end against the real API (`apps/web/e2e-stack`, run in the production-smoke CI job; 4 tests pass locally and in CI) | ff5df26 |
| 7.13.3, 7.7.9 | Upgrade test from the previous release (`scripts/upgrade_check.py`, job in `nightly-load.yml`); rehearsed locally, 10/10 checks | ee92762 |
| 7.12.2, 7.12.5, 7.12.6 | Queue board in hi/mr at 1600–320 px with axe; keyboard-only acknowledge and call-next; Start consult opens the case sheet (7 → 6 clicks); `docs/STAFF_SPEED.md` | d6dde23 |
| 7.5.2 (engineering), 7.15.3 | Full privacy notice `/privacy/<hospital>` (en/hi/mr, draft until counsel approves, reviewed like consent copy), grievance contact setting (migration 0033), QR poster in Settings | e156acf |

Local verification on 24 Oct: API 1,083 tests pass (with PostgreSQL and Redis, migrations up,
down, up); web 136 unit tests, coverage above the gate; browser specs touched by G3 pass.

### Left to do in G3 (engineering), in this order

1. **7.8.4 index review on a million visits — in progress, not committed.**
   `services/api/tests/perf/index_review.py` (untracked; lint clean) seeds a scratch database
   and drives every hot route and worker job, then prints `pg_stat_statements`. A scratch
   PostgreSQL is **already seeded** (1,000,601 visits, 400k patients, 3.7M visit events, 2.9M
   audit rows): container `osa-scale-pg` on `127.0.0.1:55434` (superuser
   `postgres`/`scale-postgres`; roles `osa_app`/`scale-app`, `osa_migrator`/`scale-migrator`,
   `osa_worker`/`scale-worker`), with `pg_stat_statements` and `auto_explain` (20 ms). The
   first run failed only because the seed did not advance `patient_number_counters`; the tool
   now does (attach to the seeded hospital, never delete). Run it with the environment in
   "Commands" below and `FRESH=0`, read the slowest statements and `docker logs osa-scale-pg`
   for plans. Suspects from reading the code: the 15-minute close-out
   (`UPDATE visits ... WHERE service_date < today AND status = ...`, no hospital filter) and the
   30-second monitoring gauges (`status = 'queued'` across hospitals) may seq-scan a million
   rows; a partial index on open statuses would fix both. Then: migration 0034 for what the
   review proves, query-count tests for queue, case-sheet and analytics routes (plan 7.8.4
   "gates in CI"), results in `docs/SCALING.md`, commit the tool.
2. **7.0.6 proof**: the three load-sensitive specs already wait for content, not time; the
   "done when" is 10 consecutive full e2e runs with 2 workers and no retry
   (`npx playwright test --workers=2 --retries=0`, then `--grep @timing --workers=1`).
   Restore `docs/screenshots` afterwards (`git checkout -- docs/screenshots`).
3. **Plan and docs**: section 11 of the plan (progress table) still says G3 workstreams "not
   started"; update it from the table above. `docs/OPD_Saathi_Documentation.pdf` is updated
   only at v1.0.0 (7.15.5).
4. **Open the G3 pull request** (feat/phase-7-g3 → main), wait for all checks including the
   two nightly workflows, fix what they find, merge (squash or merge as #35 did).
5. **Dependency pull request** (below): rebase on `main` after G3 merges, add its CHANGELOG
   lines, open it, merge when green, close #49–#58 with the reasons.

Then G3's engineering is complete. G3 itself passes only with the items owned by people
(plan section 5): penetration test closed (7.4.6), clinical and native-speaker sign-off
(7.6.1, 7.6.2, including every Hindi/Marathi string added in G3 and the privacy notice), notices
and contracts signed (7.5), usability test with severe issues fixed (7.12.1), real-device
HTTPS check (7.13.1), staff trained (7.15.4). Blocked on outside parties: real SMS (7.11.6,
provider and DLT), more languages (7.11.10, reviewers), staging and standby servers (7.14.1,
7.7.6, hardware), ABDM (7.10.2–3, months). G4 (pilot in a hospital, then v1.0.0) is field work;
its engineering (a shadow-mode switch and a pilot measurement report) is worth building only
once a hospital agrees to a pilot.

## Dependency updates: branch `deps/2026-10-24` (committed locally, see below)

Worktree `../OPD-Sathi-deps` (from `main`), commit 9952ab6, pushed as `deps/2026-10-24`.
Taken: react-query 5.104.5 (#58), eslint-plugin-react-refresh 0.5.7 (#51), TypeScript 5.9.3
pinned `~` (#49; fixed a real type error in `src/lib/auth.ts`), axe 4.14 (#54; fixed the
language tiles' accessible names, WCAG 2.5.3), the Semgrep lock regenerated with uv (pydantic
2.14.1 + pydantic-core 2.50.1, bracex 3.2; verified installing on Linux with hashes). Not
taken: jsonschema (#50), wrapt (#52), exceptiongroup (#56) — capped by Semgrep; plugin-react 6
(#57) needs Vite 8. `dependabot.yml` ignores updated. Web unit 115/115 and the affected
browser specs pass on that branch.

**Regenerating the Semgrep lock:** `uv pip compile services/api/requirements-security.in
--python-version 3.12 --generate-hashes --output-file services/api/requirements-security.lock.txt
--upgrade-package <name>`, then put back the hand-written `pywin32` stanza from `main`
(comment, `pywin32==312 ; sys_platform == "win32"` and its hashes): compiling on Windows drops
the marker. `--universal` rewrites other markers; do not use it.

## Commands (this machine)

```sh
# API tests against throwaway PostgreSQL + Redis (osa-g3-pg :55433, osa-g3-redis :56379)
bash <scratchpad>/api-tests.sh        # or replicate: CI backend job env, see .github/workflows/ci.yml
# Production-mode stack kept running (project osa-smoke, https://localhost:8444):
KEEP=1 SMOKE_STATE_FILE=/tmp/state.json SMOKE_HTTPS_PORT=8444 SMOKE_HTTP_PORT=8081 \
  PYTHON=.venv/Scripts/python.exe bash scripts/prod_smoke.sh
# Real-API browser suite against it (Node from nvm; npm must be on PATH for Playwright):
cd apps/web && PATH="/c/Users/lenovo/AppData/Local/nvm/v24.11.0:$PATH" \
  STACK_STATE=/tmp/state.json node node_modules/@playwright/test/cli.js test -c playwright.stack.config.ts
# Failure drills: SMOKE_HTTPS_PORT=8444 SMOKE_HTTP_PORT=8081 PYTHON=.venv/Scripts/python.exe bash scripts/failure-drills.sh
# Index review against osa-scale-pg (already seeded):
#   DATABASE_URL/TEST_DATABASE_URL=postgresql+asyncpg://osa_app:scale-app@127.0.0.1:55434/osa
#   TEST_MIGRATIONS_DATABASE_URL=postgresql+asyncpg://osa_migrator:scale-migrator@127.0.0.1:55434/osa
#   WORKER_DATABASE_URL/TEST_WORKER_DATABASE_URL=postgresql+asyncpg://osa_worker:scale-worker@127.0.0.1:55434/osa
#   REDIS_STATE_URL=redis://127.0.0.1:56379/2 SCALE_SUPERUSER_URL=postgresql://postgres:scale-postgres@127.0.0.1:55434/osa
#   cd services/api && ../../.venv/Scripts/python.exe -m tests.perf.index_review
```

Containers left running for the next session: `osa-scale-pg`, `osa-g3-pg`, `osa-g3-redis`,
the `osa-smoke` stack (all synthetic; remove with `docker rm -f` / `docker compose -p
osa-smoke down -v` when done). The demo stack (`opd-saathi-*`, port 8080) is the owner's: do
not touch it.

## How the work is done here (conventions that matter)

- **Commits and pull requests carry no AI attribution** (no `Co-Authored-By` trailer, no
  "Generated with" line). Commit as the owner's configured identity
  (`git -c user.name=SHASHI_DEV -c user.email=shashitherider2004@gmail.com commit`); never
  change global git config. Never commit `APP_ENV=development.txt` (real local secrets) or the
  pitch files.
- **New components need an ADR and the owner's approval** (ADR 001). Approved so far: pyotp,
  prometheus-client, Prometheus + Alertmanager (no Grafana in production), pgBackRest, Trivy,
  OWASP ZAP, Schemathesis, cosign, k6, `@vitest/coverage-v8`, `fhir.resources` (tests only).
- **CI is the authority**: `ci.yml` (backend, frontend, production-smoke + real-API e2e,
  images, security) on every push and pull request; `nightly-security.yml`,
  `nightly-load.yml` (load, real install, upgrade) and `nightly-failures.yml` (drills)
  nightly and on pull requests that touch what they test.
- **Windows checkouts**: `.gitattributes` keeps shell scripts LF and CI checks every `.sh`
  stays executable. Heredocs in Git Bash mangle backslashes and Devanagari: write patch
  scripts to files and read/write UTF-8 explicitly.
- **Local checks**: `ruff check services/api/app services/api/tests services/api/scripts
  scripts`, `mypy --strict services/api/app`, `pytest services/api/tests` (integration tests
  need PostgreSQL and Redis), `tsc -b`, `eslint .`, `vitest run --coverage`,
  `scripts/check-locales.mjs` in `apps/web`.
- **Generated files CI compares**: `apps/web/src/lib/api-types.ts` (after any API change:
  `python services/api/scripts/write_openapi.py && (cd apps/web && npm run types)`),
  `docs/paper/` (`scripts/make_paper_forms.py`), `docs/print/` (`scripts/make_print_pack.py`).
- **Translations**: every new screen string goes into `en`, `hi` and `mr`; a test fails on a
  missing key, a changed placeholder or "???". Two regions on one screen need different names
  in every language (axe `landmark-unique`).
