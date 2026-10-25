# Phase 7 handoff: G3's engineering is done; the gate waits for people

For whoever continues Phase 7 (`docs/PHASE_7_PRODUCTION_PLAN.md`). Updated on 25 Oct 2026, at the
end of G3's engineering. Read this first, then the plan's section 4 for each task's "done when"
and section 11 for where every workstream stands.

## State of `main`

| Gate | Pull request | What it delivered |
|---|---|---|
| G0 Fixed | #17 | Worker SMS without patient keys, SMS opt-in by the patient's own tick, settings wired, images pinned |
| G1 Secure build | #18 | Production overlay and HTTPS, staff account security and MFA, key rotation, least privilege, audit log screen, Trivy/ZAP/Schemathesis, threat model, ASVS L2 self-assessment |
| G2 Operable | #35 | Prometheus/Alertmanager with 19 tested alerts, pgBackRest backups with a restore drill on every change, 180-day logs, Settings → System, k6 load tests, signed releases, paper fallback pack, nightly real install |
| G3 Pilot-ready, engineering | #59 | The table below |
| Dependencies | #36, #48, then `deps/2026-10-24` | Updates tested together; React 19 (#48); the next set in its own pull request after G3 (below) |
| Release | tag `v0.7.0` | First release, cut on `main` at #48 |
| After G3 | #72, #73, #74, then the complete guide | The team's feature guide rewritten (18 pages) and three icon fixes; the OPD Saathi logo in the app; the reports counted per day (7.8.5, migration 0035); `docs/OPD-Saathi-Complete-Guide.pdf` (19 pages, everything in one place) |

## G3: what was built (pull request #59)

| Task | What | Commit |
|---|---|---|
| 7.11.1 | Doctor's own notes, versioned, on the PDF | 5089683 |
| 7.11.2 | Review of systems, off by default | a854838 |
| 7.11.3 | Results over time from accepted readings | ab0b5b4 |
| 7.11.4 | Re-issue a lost OPD card | a4c82cf |
| 7.11.5 | AI-settings screen | 6827602 |
| 7.11.7, 7.5.3 | Who is answering for the patient | 631ebc8 |
| 7.11.8, 7.11.9, 7.11.11, 7.8.4 (decision) | LIMITATIONS.md, RAG_DESIGN.md, ADRs 013–015 | cf1ed4a |
| 7.13.6 | Web coverage gate (ADR 016) | 9442a45, e0b3764 |
| 7.10.1 | FHIR R4 export of a visit (ADR 017) | 6af3ed8 |
| 7.14.7 | Per-hospital switches: SMS messages (off by default) and phone check-in (migration 0032) | 9a81b1d |
| 7.12.4 | Queue board fully translated (en/hi/mr) | bd6321d |
| 7.7.8, 7.13.4 | Failure drills nightly; `RedisDown` could never fire, fixed with `ResilientWorker` | 3ca4b01 |
| bug | Deadlock between session renewal and a password change | 95bcf9a |
| 7.15.1–7.15.3 | `docs/print` (staff cards, patient signs, CI `--check`), installation check in DEPLOYMENT.md | cc0b088 |
| 7.13.2 | End to end against the real API (`apps/web/e2e-stack`, in the production-smoke job) | ff5df26 |
| 7.13.3, 7.7.9 | Upgrade test from the previous release (`scripts/upgrade_check.py`, `nightly-load.yml`) | ee92762 |
| 7.12.2, 7.12.5, 7.12.6 | Board in hi/mr at 320–1600 px with axe, keyboard-only staff work, one click less to the case sheet | d6dde23 |
| 7.5.2, 7.15.3 | Privacy notice (en/hi/mr, draft until counsel approves), grievance contact (migration 0033), QR poster | e156acf |
| 7.8.4 | Index review on a million visits: migration 0034, query-count gates in CI, `docs/SCALING.md` | 6766e3d |
| 7.0.6 | Ten consecutive full browser runs, 2 workers, no retry (155 specs and the timing spec) | none needed |
| CI | The AI-switch e2e test crashed on the privacy card beside it (red since e156acf); a date test failed every evening UTC; Lighthouse failed on one noisy run | 2deda3f, 2025d88, 41bc08d |

On the pull request, `nightly-failures.yml` (drills) and `nightly-load.yml` (real install,
upgrade, load) ran in CI for the first time; drills, install and upgrade passed first time.

## What G3 still needs, from people (plan section 5)

Penetration test closed (7.4.6); clinical and native-speaker sign-off (7.6.1, 7.6.2), including
every Hindi and Marathi string added in G3 and the privacy notice; notices, consent texts,
contracts, retention schedule and breach procedure signed (7.5); usability test with real people
and severe issues fixed (7.12.1); real devices over HTTPS on the hospital LAN (7.13.1); staff
trained (7.15.4); the operator guide used by someone outside the team, the cards in training,
the signs printed (7.15.1–7.15.3 "done when"). Blocked on outside parties: real SMS (7.11.6,
provider and DLT), more languages (7.11.10, reviewers), staging and standby servers (7.14.1,
7.7.6, hardware), ABDM (7.10.2–3, months). G4 (a pilot in a hospital, then `v1.0.0`) is field
work; its engineering (a shadow-mode switch and a pilot measurement report) is worth building
only once a hospital agrees to a pilot.

## Next engineering work, most valuable first

Done since: the per-day analytics rollup (7.8.5, migration 0035). The 5-minute refresh recounts
today and yesterday, 0.1–0.2 s at a million visits where the full refresh took 10 s, over the
worker's 5-second statement limit, and a nightly rebuild recounts every day.
`docs/SCALING.md`, "The reports rollup, per day".

1. **Audit search by action alone** reads the whole log (about 5 s, the statement limit, on a
   real million visits). Either require a date range with an action search, or a trigram index
   (`pg_trgm`, a new component: ADR and the owner's approval first). The runbook already asks
   investigators for a date range.
2. **Load and soak results into the release notes** of the next release candidate (7.13.5).
3. **An error boundary for staff screens.** A card that throws while rendering takes the whole
   screen to the router's default error page (seen in the e2e crash above); a per-screen
   boundary with a "Reload" button would contain it.

## Dependency updates: branch `deps/2026-10-24`

Worktree `../OPD-Sathi-deps`, one commit (9952ab6) on `main` before G3; to be brought up to date
with `main` after #59 merges, given its CHANGELOG lines, and merged in its own pull request.
Taken: react-query 5.104.5 (#58), eslint-plugin-react-refresh 0.5.7 (#51), TypeScript 5.9.3
pinned `~` (#49; fixed a real type error in `src/lib/auth.ts`), axe 4.14 (#54; fixed the
language tiles' accessible names, WCAG 2.5.3), the Semgrep lock regenerated with uv (pydantic
2.14.1 + pydantic-core 2.50.1 for #53, bracex 3.2 superseding #55). Not taken: jsonschema (#50),
wrapt (#52), exceptiongroup (#56), capped by Semgrep; plugin-react 6 (#57) needs Vite 8.
`dependabot.yml` ignores updated. When it merges, Dependabot pull requests #49–#58 are closed
with these reasons.

**Regenerating the Semgrep lock:** `uv pip compile services/api/requirements-security.in
--python-version 3.12 --generate-hashes --output-file services/api/requirements-security.lock.txt
--upgrade-package <name>`, then put back the hand-written `pywin32` stanza from `main`
(comment, `pywin32==312 ; sys_platform == "win32"` and its hashes): compiling on Windows drops
the marker. `--universal` rewrites other markers; do not use it.

## Commands (this machine)

```sh
# API tests against throwaway PostgreSQL + Redis (osa-g3-pg :55433, osa-g3-redis :56379) with
# the CI backend job's variables (ci.yml): DATABASE_URL, TEST_DATABASE_URL,
# TEST_MIGRATIONS_DATABASE_URL, TEST_WORKER_DATABASE_URL on 127.0.0.1:55433 with the
# ci-osa-*-password roles; REDIS_STATE_URL and REDIS_CACHE_URL on 127.0.0.1:56379/0 and /1.
# The root .env is the owner's demo: also export WORKER_DATABASE_URL="" and DATABASE_READ_URL="",
# or a test could reach the demo database on :5432.
.venv/Scripts/python.exe -m pytest services/api/tests
# Production-mode stack (project osa-smoke, https://localhost:8444):
KEEP=1 SMOKE_STATE_FILE=/tmp/state.json SMOKE_HTTPS_PORT=8444 SMOKE_HTTP_PORT=8081 \
  PYTHON=.venv/Scripts/python.exe bash scripts/prod_smoke.sh
# Real-API browser suite against it (Node from nvm; npm must be on PATH for Playwright):
cd apps/web && PATH="/c/Users/lenovo/AppData/Local/nvm/v24.11.0:$PATH" \
  STACK_STATE=/tmp/state.json node node_modules/@playwright/test/cli.js test -c playwright.stack.config.ts
# Failure drills: SMOKE_HTTPS_PORT=8444 SMOKE_HTTP_PORT=8081 PYTHON=.venv/Scripts/python.exe bash scripts/failure-drills.sh
# The 7.0.6 proof: npm run e2e with --retries=0, ten times; restore docs/screenshots afterwards.
# Index review: services/api/tests/perf/index_review.py (docstring); scratch database below.
```

Containers: `osa-scale-pg` (127.0.0.1:55434, PostgreSQL 16.14 with `pg_stat_statements` and
`auto_explain`, seeded with a million visits and at migration 0034; superuser
`postgres`/`scale-postgres`, roles `osa_app`/`scale-app`, `osa_migrator`/`scale-migrator`,
`osa_worker`/`scale-worker`; Redis database 2 of `osa-g3-redis`), `osa-g3-pg`, `osa-g3-redis` and
the `osa-smoke` stack: all synthetic; remove with `docker rm -f -v` / `docker compose -p
osa-smoke down -v` when done. They stop when Docker Desktop stops; `docker start` them again.
The demo stack (`opd-saathi-*`, port 8080) is the owner's: do not touch it.

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
  nightly and on pull requests that touch what they test. CI's clock is UTC: a test that
  compares with a date the application sets uses the hospital's day (`care_date()`).
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
- **Browser tests mock every API call they rely on**: when a screen gains a card, the specs
  that open that screen need its response too (a catch-all that answers the wrong shape can
  crash the page).
