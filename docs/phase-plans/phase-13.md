# Phase 13 implementation plan

## Scope

Harden the local clinic stack, validate tenant/auth boundaries and dependency risk, measure load and failure behavior, and document operating procedures. This phase touches deployment and retention behavior; destructive operations must be opt-in, scoped, and tested on synthetic data.

## Build sequence

1. **13.1 Security scans** — run Bandit, pip-audit, npm audit, Semgrep, Gitleaks, and Trivy. Fix high/critical findings; document medium findings and tool limitations.
2. **13.2 Abuse tests** — verify JWT rejection cases, cross-tenant access, request-size limits, and WebSocket limits. Add schema-driven endpoint fuzzing when Schemathesis is available.
3. **13.3 Supply chain** — pin runtime dependencies, preserve lockfile hashes, and generate an SBOM.
4. **13.4 Database** — set a five-second statement timeout; document connection budgets and read-only analytics routing. Partitioning and PgBouncer require migration/performance measurements before production use.
5. **13.5 Redis** — separate durable state (`noeviction`, AOF every second) from bounded evictable cache (`allkeys-lru`).
6. **13.6–13.7 Performance** — add reproducible k6 scenarios and query-count guards; measure on a documented reference machine.
7. **13.8 Chaos** — exercise Redis, worker, database, and provider failures; require explicit opt-in for destructive service restarts.
8. **13.9 Observability** — expose operational metrics and add optional Compose dashboards without collecting patient text or identifiers.
9. **13.10 Backup and restore** — create restricted, checksummed PostgreSQL and Redis snapshots and verify restoration in an isolated target.
10. **13.11 Privacy and retention** — map data, define retention limits, and add dry-run-first retention/anonymization commands with tests and documentation.

## Security and privacy constraints

- Use synthetic data in security, load, chaos, backup, and restore gates.
- Never print credentials, raw patient text, or access tokens in reports.
- Do not delete or anonymize data without explicit `--apply`; enforce tenant scope and report affected row counts first.
- Do not claim clinical privacy compliance or production readiness from local tests alone.

## Current progress

- **Initial audit:** authenticated JWT and tenant-isolation tests already exist; backend runtime requirements have a hash-locked file and frontend dependencies have `package-lock.json`. `Makefile` targets for load, chaos, scan, backup, and restore were placeholders. `docs/PRIVACY.md` was incomplete; no Phase 13 infrastructure directory or report existed.
- **13.1 partial:** Bandit reported no medium/high API findings at `-lll`; `pip-audit` found no known vulnerabilities in the pinned Python requirements; `npm audit --audit-level=high` found zero vulnerabilities. Semgrep 1.178.0's `p/security-audit` pack found 0 issues in `services/api/app/core/auth.py` (79 rules applied); the broader API/web source scan did not finish within the available run window. Gitleaks found no leaks in `services/api/app` (1.64 MB) and `apps/web/src` (492 KB); broader worktree and Git-history scans did not finish within the available window. Trivy/ZAP were not completed.
- **13.2 partial:** existing JWT rejection and tenant-isolation suites were reviewed; targeted security, rate-limit, and replay tests passed (22). Dedicated Schemathesis fuzzing and WebSocket abuse coverage remain.
- **13.3 partial:** preserved hash-locked Python requirements and the npm lockfile; generated CycloneDX SBOMs (`docs/security/python.cdx.json`: 105 packages; `web.cdx.json`: 751 packages). Pinning workflow/action revisions remains.
- **13.4 partial:** application PostgreSQL connections now enforce a five-second statement timeout; a live PostgreSQL integration test verifies it. PgBouncer, read-only analytics routing, and monthly partitions remain.
- **13.5 complete in code:** Compose separates persistent `redis` state (AOF every second, `noeviction`) from a bounded cache Redis (`allkeys-lru`); the API passes the cache connection to the LLM provider. Compose config validation passed.
- **13.6–13.7 partial:** ran a 25-VU health-only k6 smoke against the local API before the isolation requirement was clarified: 1,382 requests over 70 s, 0.00% failed, 19.60 requests/s, HTTP p95 13.51 ms, max 92.59 ms; all checks returned 200. This changed no database records. Host hardware was not captured. Representative intake/WebSocket/status workloads, 200-session load, soak, and N+1 query guards remain.
- **13.8:** safe provider/Redis tests passed (36). Destructive drills ran only against the labeled `opd-saathi-phase13` Compose project on alternate loopback ports with a fresh synthetic database. Redis stop/recovery returned readiness 503 then 200; worker stop left the API ready and the worker restarted; PostgreSQL stop/recovery returned 503 then 200. Shared Compose services and volumes were not targets.
- **13.9 partial:** added an opt-in (`METRICS_ENABLED=false` by default) Prometheus HTTP request counter and fixed-bucket latency histogram using method, status, and route-template labels; added a loopback-only Prometheus/Grafana Compose profile, API dashboard, and 2% error-rate/1.5 s HTTP-p95 alert rules. The endpoint is intended for a private Compose network. Focused app tests (12), Ruff, strict mypy, Compose config, and dashboard JSON validation passed. Metrics are process-local under multi-worker Gunicorn, and provider, queue, WebSocket, outbox, delivery, and OpenTelemetry signals are still absent; this does not pass the full observability gate.
- **13.10:** checksummed backup of the isolated synthetic stack passed. PostgreSQL restored into an isolated scratch database and matched the 100-visit manifest. Redis RDB loaded in a profile-scoped throwaway Redis on tmpfs; the synthetic restore sentinel matched.
- **13.11 partial:** documented the data map and added a tenant-scoped, dry-run-first 30/90-day purge/pseudonymization command. A PostgreSQL integration test confirms dry-run preserves data and `--apply` removes old answers/summaries and direct identifiers. It is not scheduled, and stable patient IDs/visit/audit history remain; clinic approval and a retention scheduler are still needed.
- **Regression:** complete API suite passes (758 tests, 32.05 s; three warnings). Strict mypy and Ruff pass for observability/readiness changes. Focused app tests pass (13). Readiness dependency probes are bounded and return 503 during a dependency outage rather than hanging.

## Exit assessment

Phase implementation and isolated destructive drills are complete, but this is not a production-readiness or all-gates pass. Broad security scans, schema fuzzing, representative endpoint load/soak, host-hardware capture, N+1 query guards, read-only analytics routing, PgBouncer/partition measurements, and a retention scheduler remain open. The temporary drill stack and its generated synthetic data are removed after verification; the isolated Compose override and chaos runner remain reusable.
