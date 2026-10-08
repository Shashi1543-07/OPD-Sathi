# Phase 13 — Hardening and operations (7 October 2026)

## What changed

- Added operational HTTP metrics, isolated chaos tooling, backup and restore scripts, and dry-run-first retention tooling.
- Bounded readiness database and Redis probes to one second. During dependency failure, `/readyz` now returns 503 instead of hanging.
- Added `make chaos-isolated` and `scripts/chaos_isolated.py`. The runner refuses to act unless the Compose project and isolation labels match `opd-saathi-phase13`.
- Added the isolated Compose override with dedicated named volumes, alternate loopback ports, and a temporary Redis restore profile.

## Isolated restore and chaos evidence

All destructive drills used an ephemeral Compose project, random credentials, and a database populated only by deterministic fictional demo records (100 visits). No destructive command targeted the normal shared Compose project. The temporary project ran on PostgreSQL port 15432, Redis port 16379, cache Redis port 16380, and API port 18000.

| Drill | Result |
|---|---|
| PostgreSQL, Redis, and visit manifest backup checksums | Passed |
| PostgreSQL restore to scratch database | Passed; restored visit count matched 100 |
| Redis RDB restore to a temporary tmpfs-backed Redis | Passed; synthetic sentinel matched |
| Redis stop/recovery | Passed; readiness 503 during outage, 200 after recovery |
| Worker stop/recovery | Passed; API remained ready and worker restarted |
| PostgreSQL stop/recovery | Passed; readiness 503 during outage, 200 after recovery |

The API regression suite passed on the isolated database: 758 passed, three warnings. Focused readiness tests passed (13). Ruff and strict mypy passed for the readiness/observability changes.

After evidence capture, the isolated Compose project and its named volumes, temporary backup directory, RDB fixture, and `.env.phase13` were removed. Shared Compose services were checked separately and left running.

## Gate status and remaining work

| Area | Result |
|---|---|
| Bandit, pip-audit, npm audit | Passed at configured thresholds; no high/critical findings reported |
| Semgrep / Gitleaks | Focused scans clean; broader source/history scans did not complete |
| Trivy / ZAP / Schemathesis | Not completed |
| Load | Existing health-only smoke: 1,382 requests, 0 failures, 19.60 req/s, p95 13.51 ms. It was run before isolation was required and made no database changes. Representative intake/socket/status, 200-session, and soak runs remain |
| Query count / scale | N+1 guards, PgBouncer, read-only analytics routing, and partition measurements remain |
| Retention | Dry-run-first command and tests exist; scheduler and clinic-approved retention schedule remain |
| Observability | HTTP metrics and local dashboard exist. Metrics are process-local; provider, queue, WebSocket, outbox, and OpenTelemetry metrics remain |

The phase implementation and requested isolated drills are complete. These remaining gates mean this report does not claim production readiness or a full all-gates pass. Use only synthetic data for future security, performance, backup, restore, and chaos work.

## Follow-up scan and performance update (7 October 2026)

| Gate | Latest result |
|---|---|
| Bandit | Passed; the `notify_token` boolean event flag is explicitly documented as a false positive (B105) |
| pip-audit | No known vulnerabilities in the API and security-tool lockfiles |
| npm audit | 0 vulnerabilities |
| Gitleaks history scan | No leaks across 82 commits after recording four exact synthetic test-fixture fingerprints in `.gitleaksignore`; no path-wide or rule-wide suppression |
| Semgrep | Auto-rule scan stalled before returning results and was stopped; not counted as passed |
| Trivy, ZAP, Schemathesis | Not completed |
| Browser payload | Route splitting lowered the production entry from 576.91 kB to 427.86 kB minified (174.16 kB to 135.36 kB gzip); six Lighthouse runs scored kiosk 88–89 and demo 96, accessibility 100 |
| Representative load/soak | Still open; the only later k6 run is health-only (1,387 requests, 0 failures, p95 5.12 ms) |

The Gitleaks suppressions are limited to the fingerprints reported for synthetic test strings; they do not suppress those test files or the generic-key rule. The active local `.env` was not scanned or printed. This update does not close the manual policy, production deployment, query/scale, full workload, or telemetry gates.
