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

With the defaults that is 2 x 20 + 15 + 5 = 60. Raising `WEB_CONCURRENCY` to 4 needs `DB_POOL_MAX` of 20 at most (80 + 20 = 100 is already the limit, so lower it to 15) or a higher `max_connections`. PgBouncer is not used (ADR 013: the trigger to add it, and the test that keeps transaction pooling possible).

`DATABASE_READ_URL`, when set, is used for the one pure-read heavy query: the report CSV export. Every other report route also writes an audit entry in the same transaction and stays on the primary. Leave it empty unless a replica exists; the export then reads from the primary.

## Measured results

- Phase 11's summary query measured 1.88 ms p95 over 30 iterations against the seeded 100,000-visit Compose fixture. This is a local query benchmark, not a concurrent request capacity result.
- Phase 12's Lighthouse CI run measured kiosk performance 85–86, accessibility 100, and best practices 96; the status page scored 94–95, accessibility 100, and best practices 96.
- After Phase 14 route splitting, the production entry chunk is 427.86 kB (135.36 kB gzip); six Lighthouse runs scored kiosk performance 88–89 and demo performance 96 with accessibility 100. This is single-browser lab evidence, not a clinic-device guarantee.
- `make load-test` (`scripts/load-test.sh`, k6 scenario `infra/k6/opd-day.js`) is the real-flow
  load test; see "Load and soak tests" below.

## Growth path and unfinished work

1. The optional local Prometheus/Grafana profile and HTTP request metrics are described in `docs/OBSERVABILITY.md`. Before relying on metrics operationally, replace the process-local exporter with a multi-worker-safe collector and add provider, queue, WebSocket, outbox, delivery, and database-pool metrics.
2. Replace the full refresh of the analytics rollup with a per-day one before a busy hospital's history grows large (see "Index review on a million visits" below; task 7.8.5).
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

## Index review on a million visits (Phase 7, task 7.8.4)

**How (25 Oct 2026).** `services/api/tests/perf/index_review.py` seeds a scratch database with
a district hospital's history, puts five patients through the real kiosk interview, then drives
every hot route and worker job through the real application twice (the warm round counts);
`pg_stat_statements` and `auto_explain` show what each statement costs and how it is planned.
The data: 1,000,000 past visits over 500 days (two departments, 1,000 a day each), 400,000
patients, a case sheet and a consent for every visit, 11.5 million interview answers, 3.7
million timeline events, 2.9 million audit entries, 32,000 danger-sign alerts, and 600 patients
waiting today. PostgreSQL 16.14 (the production image) with its default settings (128 MB shared
buffers, 4 MB work memory), on a laptop's 4 GB Docker VM: compare the numbers with each other,
not with a server. The seed writes three audit entries a visit where the application writes
about a dozen (every case-sheet screen read, lookup and queue action is audited), so a real
million visits has a log about four times this size.

**What the review found, and migration `0034_index_review`.** Five statements read a whole
table where a few index pages would do. Each now has an index, checked with `EXPLAIN ANALYZE`
in the role and row-level security scope the application uses, and again as a prepared
statement run more than five times (PostgreSQL keeps choosing the partial indexes):

| Statement | Was | Now | Index |
|---|---|---|---|
| Close-out (every 15 minutes, and whenever an API process starts, which Gunicorn's recycling makes every few minutes under load): visits still registering or awaiting confirmation on an earlier day | Whole index of every visit: 18–24 ms, 1.5 s from a cold cache | 0.04 ms | `ix_visits_open`: visits not yet closed (16 kB) |
| Close-out: wipe the phone numbers kept for queue messages | Every visit read: 276 ms | 0.6 ms | `ix_visits_notify_phone` (8 kB) |
| Register, newest first (nurse console, Patients) | Every visit sorted, partly on disk: 260–383 ms | 2–3 ms | `ix_visits_registered` (28 MB) |
| Audit search by record | Whole log read: 157–324 ms | 0.1 ms | `ix_audit_resource` (163 MB) |
| Audit search by person | Whole log read: 74–86 ms | 0.1 ms | `ix_audit_actor` (41 MB) |
| Queue board, all departments (administrator) | Every visit read: 62–82 ms | 3 ms | `ix_visits_open` |

The migration builds them in 4.7 seconds on this data (downgrade 1.6 s); a hospital with a
longer audit log takes longer, and upgrades run outside OPD hours (`docs/DEPLOYMENT.md`).

**Whole requests through the application, warm round, before and after the migration (ms):**

| Route or job | Before | After |
|---|---|---|
| Status page | 3.6 | 3.9 |
| Queue board (one department; waiting only) | 23.5; 28.7 | 24.0; 22.2 |
| Queue board, all departments | 90.8 | 30.2 |
| Open alerts | 15.6 | 12.9 |
| Register today; 30 days; newest | 62.1; 74.8; 266.3 | 10.0; 11.2; 12.4 |
| Patient lookup by OPD card; by phone | 11.4; 10.6 | 12.5; 12.0 |
| Case-sheet screen (case sheet, history, answers, notes, papers, results, timeline) | 11.0–16.6 each | 9.5–15.5 each |
| FHIR export of a visit | 21.4 | 15.9 |
| Reports summary; timeseries (30 days) | 12.6; 11.5 | 11.3; 11.2 |
| Audit log newest; by record; by person | 11.9; 99.6; 83.6 | 13.1; 10.8; 10.8 |
| Call next; start; complete | 26.6; 18.9; 18.0 | 29.0; 22.0; 17.8 |
| Worker: outbox relay; monitoring gauges | 123; 24 | 149; 18 |
| Worker: close-out | 395 | 9 |
| Worker: queue rebuild (at start and after a close-out that closed visits) | 780–944 | 736–847 |
| Worker: analytics rollup refresh (every 5 minutes) | 11,772–12,369 | 9,695–11,120 |

After the migration no statement of a route or a frequent job averages more than 11 ms. What
the review measured and left as it is:

- **The analytics rollup refresh is the cost that grows** (task 7.8.5). Every 5 minutes it
  recomputes every day of the hospital's history: here 10 seconds of query, reading 11.5 million
  answers, a million visits and a million case sheets, and writing about 1 GB of temporary files
  with the default work memory (with 64 MB: 9.0 s, so memory is not the fix). That is roughly
  an hour of database time a day at a million visits, and it grows with every day kept; at
  100,000 visits it is about a second. No index can make a full recount cheap. The decision
  from this measurement: keep the 5-minute refresh for a pilot, and before a busy hospital's
  history passes about 250,000 visits (four months at 2,000 patients a day) replace it with a
  per-day rollup that recomputes only today, yesterday and any day whose visits changed. A
  read replica (`DATABASE_READ_URL`) would move this cost, not remove it.
- **Audit search by action alone** (for example every `auth.login.failed`) reads the whole log
  newest first: 0.9–1.4 s here, so about 5 seconds, the statement time limit, on a real million
  visits. "Contains" cannot use an ordinary index. With a record, a person or a date range the
  search is fast; the investigation runbook says to give a date range. A trigram index
  (`pg_trgm`) would serve it but is a new component (ADR 001, the owner's decision).
- **The queue rebuild** costs one Redis round trip per waiting patient (the database part takes
  2 ms). It runs when the worker starts and after a close-out that closed visits, not on a
  timer.
- **Open alerts** and the urgent-alert gauge read every alert of the hospital: 2–5 ms for 32,000.
  An index on open alerts is worth adding if a hospital's alerts reach a few hundred thousand.

**Gates in CI.** `services/api/tests/integration/test_query_counts.py` counts the SQL statements
of each screen staff keep open: the queue board, open alerts, the doctor's list and the
register with 3 and then 8 patients waiting; the seven requests of the case-sheet screen for a
patient with one and then three earlier visits; the reports for an empty and then a busy month.
A count that grows with the data (a query per row) fails, and so does a count over its budget
(today 3 to 7 statements a request, all included). Planting one extra query per waiting
patient in the board's repository made it fail as it should (board 9 → 14).
