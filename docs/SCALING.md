# Scaling and operating notes

## Current local architecture

- The API runs `WEB_CONCURRENCY` Gunicorn/Uvicorn workers (default 2; `services/api/gunicorn.conf.py`), each recycled after about 2,000 requests (with jitter, so never all at once) and given 30 seconds to finish requests on restart. It is stateless apart from PostgreSQL and Redis.
- PostgreSQL is the source of truth for visits, queue state, tenants, audit events, and analytics. API database connections use `pool_pre_ping` and a five-second PostgreSQL statement timeout.
- Redis state is persistent (AOF with `appendfsync everysec`) and uses `noeviction`. It carries queues, kiosk sessions, rate limits, and idempotency records.
- Redis cache is a separate, bounded service with `allkeys-lru`. LLM provider caching uses this connection. Cache loss must not be treated as loss of clinical records.
- The worker uses a separate restricted PostgreSQL role and the durable state Redis.
- Tenant boundaries are enforced by PostgreSQL row-level security as well as application authorization.

Compose defaults expose PostgreSQL on `127.0.0.1:5432`, state Redis on `127.0.0.1:6379`, cache Redis on `127.0.0.1:6380`, and the API on `127.0.0.1:8000`. Do not publish these ports on an untrusted network.

## Connection budget

Each API process keeps `DB_POOL_MIN` connections open and opens up to `DB_POOL_MAX` at peak (defaults 5 and 20). The worst case is:

```
WEB_CONCURRENCY x DB_POOL_MAX   (API)
+ 15                           (worker: its default pool and overflow)
+ 5                            (migrations, backups, an operator's psql session)
must stay below PostgreSQL max_connections (100 by default)
```

With the defaults that is 2 x 20 + 15 + 5 = 60. Raising `WEB_CONCURRENCY` to 4 needs `DB_POOL_MAX` of 20 at most (80 + 20 = 100 is already the limit, so lower it to 15) or a higher `max_connections`. PgBouncer is not configured yet (task 7.8.4).

`DATABASE_READ_URL`, when set, is used for the one pure-read heavy query: the report CSV export. Every other report route also writes an audit entry in the same transaction and stays on the primary. Leave it empty unless a replica exists; the export then reads from the primary.

## Measured results

- Phase 11's summary query measured 1.88 ms p95 over 30 iterations against the seeded 100,000-visit Compose fixture. This is a local query benchmark, not a concurrent request capacity result.
- Phase 12's Lighthouse CI run measured kiosk performance 85–86, accessibility 100, and best practices 96; the status page scored 94–95, accessibility 100, and best practices 96.
- After Phase 14 route splitting, the production entry chunk is 427.86 kB (135.36 kB gzip); six Lighthouse runs scored kiosk performance 88–89 and demo performance 96 with accessibility 100. This is single-browser lab evidence, not a clinic-device guarantee.
- `make load-test` (`scripts/load-test.sh`, k6 scenario `infra/k6/opd-day.js`) is the real-flow
  load test; see "Load and soak tests" below.

## Growth path and unfinished work

1. The optional local Prometheus/Grafana profile and HTTP request metrics are described in `docs/OBSERVABILITY.md`. Before relying on metrics operationally, replace the process-local exporter with a multi-worker-safe collector and add provider, queue, WebSocket, outbox, delivery, and database-pool metrics.
2. Add N+1 query-count gates for queue, case-sheet, and analytics routes.
3. Test PgBouncer transaction pooling with `SET LOCAL`/RLS before adopting it. Add a separate read-only analytics engine and lag-aware behavior.
4. Partition high-volume `audit_log` and `interview_turns` only after migration rehearsals and retention requirements are approved.
5. Scale API replicas behind a TLS reverse proxy, then scale workers according to outbox age and queue processing delay. Use managed PostgreSQL/Redis with tested restore procedures for multi-clinic deployments.
6. Add per-clinic dashboards and alerts for database pool saturation, queue update delay, outbox age, and Redis memory pressure.

The local Compose stack and its smoke tests are development tools. They do not establish a production capacity, recovery objective, or security certification.

## Load and soak tests (Phase 7, tasks 7.8.2 and 7.8.3)

`scripts/load-test.sh` starts the production-mode stack, pairs 10 kiosks and 8 doctors, and runs
four kinds of user at once for `DURATION` (default 15 minutes):

| User | Default | What it does |
|---|---|---|
| Kiosk check-ins | 300 an hour (`ARRIVALS_PER_HOUR`), a district hospital's peak | Consent, identity, danger signs, every interview question with a pause, confirm |
| Doctors | 8 | Call next, start, consult (`CONSULT_SECONDS`), complete; sign in again every 8 minutes |
| Staff boards | 20 (`BOARDS`) | Refresh the queue every 5 s |
| Patients' status pages | 200 (`STATUS_PAGES`) | Refresh every 15 s |

It passes when every step's p95 is under 1.5 s, under 1% of requests fail, no token number was
issued twice in a department and day, and the worker logged no error; it also records memory,
database connections and Redis memory before and after, and the API's own p95 per route from
Prometheus (`load-reports/resources.txt`). It runs every night in GitHub Actions
(`nightly-load.yml`, 15 minutes); a soak of any length, for example `8h` across midnight so the
close-out runs under load, is started by hand from the Actions page.

**First results (23 Oct 2026, a developer laptop running the whole stack and k6 in a 4 GB Docker
VM; not capacity figures):** 6,467 requests, 0.07% failed; no token issued twice; no worker
error; memory and connections steady over the run. The API's own p95: check-in steps 50 to
95 ms, staff board 88 ms, call next 82 to 917 ms, status page 242 to 1,540 ms. An uncached
status request on the idle stack takes 38 ms (median, 68 patients waiting), so the long tails
came from the load generator and stack competing for the VM's memory and CPU. Capacity per
server size is measured on the nightly runner and on staging hardware before the pilot
(task 7.8.1), and recorded here.

**GitHub runner (23 Oct 2026; 4 vCPU, 16 GB; stack and k6 on the same machine), 15 minutes at
300 check-ins an hour, 20 boards, 200 status pages, 8 doctors:** all thresholds met. 18,102
requests, 0.02% failed; 135 tokens issued, none twice; no worker error. p95 per step: check-in
29 ms, staff board 16 ms, call next 165 ms, status page 959 ms. The API's own p95 was at most
194 ms on every route (sign-in, which hashes the password). Memory and connections steady:
API 224 to 244 MiB, PostgreSQL 53 to 96 MiB, 12 to 17 database connections. This is the
district-hospital peak on a server smaller than the one recommended for it.
