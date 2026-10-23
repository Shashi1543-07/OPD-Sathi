# Phase 7 handoff: where the work stands, and where G3 starts

For whoever continues Phase 7 (`docs/PHASE_7_PRODUCTION_PLAN.md`). Written on 24 Oct 2026, at
the end of G2. Read this first, then the plan's section 4 for each task's "done when".

## State of `main`

| Gate | Pull request | What it delivered |
|---|---|---|
| G0 Fixed | #17 | Worker SMS without patient keys, SMS opt-in by the patient's own tick, settings wired, images pinned |
| G1 Secure build | #18 | Production overlay and HTTPS, staff account security and MFA, key rotation, least privilege, audit log screen, Trivy/ZAP/Schemathesis, threat model, ASVS L2 self-assessment |
| G2 Operable | #35 | Prometheus/Alertmanager with 19 tested alerts, pgBackRest backups with a restore drill on every change, 180-day logs, Settings → System, k6 load tests, signed releases, paper fallback pack, nightly real install |
| Dependencies | #36 | The week's updates, tested together (see "Held back" below) |

Progress per workstream: the table in section 11 of the plan. Decisions: `docs/DECISIONS.md`
(ADRs 001 to 012). What changed for users: `CHANGELOG.md` (0.7.0, not yet tagged).

## Start here: G3 "Pilot-ready" (plan section 5)

Branch `feat/phase-7-g3` (from `main`). One pull request for the gate, merged when every check is
green (the owner has agreed to that). G3 passes when the penetration test is closed, clinical
and native-speaker sign-off is complete, notices and contracts are signed, the usability test
is done with severe issues fixed, the real-device HTTPS check has passed and staff are trained.
Most of that is owned by people outside engineering; the engineering work is:

1. **7.11 Finishing the product**: doctor's own notes (7.11.1), review of systems (7.11.2),
   papers timeline (7.11.3), re-issuing a lost OPD card (7.11.4), screens for APIs that exist
   (7.11.5, including an AI-settings screen), real SMS/WhatsApp (7.11.6, needs a provider
   contract), someone answering for the patient (7.11.7), patients seeing their own records
   (7.11.8), drug-interaction checks (7.11.9, needs an ADR and clinical review), more languages
   (7.11.10), the promised documents `docs/RAG_DESIGN.md` and `docs/LIMITATIONS.md` (7.11.11).
2. **7.13 Testing and quality gates**: end-to-end against the real API (7.13.2; today e2e mocks
   it), an upgrade test from the previous release (7.13.3; the nightly install's second run is a
   start), restore and failure tests nightly (7.13.4), a web coverage gate (7.13.6).
3. **7.12 Usability and accessibility** engineering parts: assistive technology pass (7.12.2),
   visual pass per screen (7.12.5), staff speed (7.12.6).
4. **7.15 Documentation**: operator guide (7.15.1), staff guides (7.15.2), patient signs
   (7.15.3).
5. **7.10 Interoperability**: FHIR export first (7.10.1); ABDM is months of certification.

## Left over from G2 (do early in G3)

- **React 19** (Dependabot #32, held back): `apps/web/src/app/PatientStatus.tsx` needs type fixes
  (`useRef` now takes an initial value; a status value may be undefined at line ~196). Upgrade
  `react`, `react-dom`, `@types/react`, `@types/react-dom` together; run the e2e and device checks.
- **7.7.8 Failure tests per case** (Redis down, PostgreSQL down, worker down, disk full), nightly,
  extending `scripts/chaos_*.py`; each with its known screen and alert.
- **7.8.4 Database capacity**: PgBouncer decision (needs an ADR and the owner's approval) and
  an index review on a million-visit dataset.
- **7.14.7 Switches per hospital** shown in Settings, risky features off by default.
- **7.14.1 Staging server** (needs hardware from the hospital) and **7.7.6 standby server**.
- **Cut the first release**: `VERSION` is 0.7.0 and `CHANGELOG.md` has its notes; tag `v0.7.0`
  on `main` to run `.github/workflows/release.yml` for the first time (`docs/RELEASES.md`).

## Dependabot queue to triage first (open on 24 Oct 2026)

Dependabot opens up to five pull requests per ecosystem each week. Triage them together on one
branch (as #36 did), never by merging them one by one: each runs the full CI, and several need
companions or are majors.

| PR | Update | Suggested |
|---|---|---|
| #37 | Development images: PostgreSQL 16.14, Prometheus 3.15, Grafana 12.4 (development only) | Take; its checks passed after a rerun (a Playwright download had timed out) |
| #40 | react-dom 19 | Take with the React 19 upgrade (left over from G2) |
| #41 | jsdom 25 → 30 (test environment) | Major: try with the web tests |
| #42 | semgrep 1.179 → 1.181 (CI-only lock) | Take if the security job passes |
| #43 | @testing-library/user-event patch | Take |
| #44 | opentelemetry-proto 1.45 (Semgrep lock) | Close: Semgrep pins 1.37 (ADR 006; now ignored in dependabot.yml) |
| #45 | click 8.5 | Take if the security job passes |
| #46 | @playwright/test 1.64 | Take; the e2e jobs verify it |
| #47 | TypeScript 5.7 → 7.0 | Major (the Go-based compiler): its own pass with typecheck, lint and build |

## Held back on purpose (do not take as routine updates)

PostgreSQL 18 (needs `pg_upgrade` or dump and restore: a planned upgrade), Redis 8 (licence
changed; review before any government deployment), Python 3.14 and Node 26 (runtime upgrades),
nginx 1.31 (mainline; stay on the stable line), opentelemetry-exporter 1.45 (Semgrep pins 1.37,
ADR 006). `.github/dependabot.yml` proposes only patches for these.

## How the work is done here (conventions that matter)

- **Commits and pull requests carry no AI attribution** (no `Co-Authored-By` trailer, no
  "Generated with" line). Commit as the owner's configured identity
  (`git -c user.name=SHASHI_DEV -c user.email=<owner's email> commit`); never change global git
  config. Never commit `APP_ENV=development.txt` (real local secrets) or the pitch files.
- **New components need an ADR and the owner's approval** (ADR 001). Approved so far: pyotp,
  prometheus-client, Prometheus + Alertmanager (no Grafana in production), pgBackRest, Trivy,
  OWASP ZAP, Schemathesis, cosign, k6.
- **CI is the authority**: `ci.yml` (backend, frontend, production-smoke, images, security) on
  every pull request; `nightly-security.yml` (signed-in DAST) and `nightly-load.yml` (load test
  and a real install on a fresh Linux machine, then an upgrade) nightly and on pull requests that
  touch what they test. The real-install job found five deployment bugs in G2: keep it.
- **Windows checkouts**: `.gitattributes` keeps shell scripts LF and CI checks every `.sh` stays
  executable (`git update-index --chmod=+x` for new scripts). Heredocs in Git Bash mangle
  backslashes: write patch scripts to files.
- **Local checks**: `ruff check services/api/app services/api/tests services/api/scripts scripts`,
  `mypy --strict services/api/app`, `pytest services/api/tests` (integration tests need
  PostgreSQL and Redis), `npm run typecheck && npm run lint && npm test` in `apps/web`,
  `bash scripts/prod_smoke.sh` (the whole production stack with backups, monitoring and a
  restore drill; `SMOKE_HTTPS_PORT`/`SMOKE_HTTP_PORT` if 8443/8080 are taken).
- **Generated files CI compares**: `apps/web/src/lib/api-types.ts` (after any API change:
  `python services/api/scripts/write_openapi.py && (cd apps/web && npm run types)`) and
  `docs/paper/` (`python scripts/make_paper_forms.py`).
- **Translations**: every new screen string goes into `en`, `hi` and `mr`; a test fails on a
  missing key, a changed placeholder or "???" (encoding damage).
