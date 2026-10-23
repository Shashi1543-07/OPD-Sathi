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
- `make load-test` runs a 25-VU `/healthz` smoke profile with k6. A local run completed 1,382 requests in 70 s with 0.00% failed, 19.60 requests/s, and 13.51 ms p95 (host hardware was not captured). It does not simulate intake completion, queue mutations, staff WebSockets, or the playbook's 200-session/10-minute capacity target.

## Growth path and unfinished work

1. The optional local Prometheus/Grafana profile and HTTP request metrics are described in `docs/OBSERVABILITY.md`. Before relying on metrics operationally, replace the process-local exporter with a multi-worker-safe collector and add provider, queue, WebSocket, outbox, delivery, and database-pool metrics.
2. Add N+1 query-count gates for queue, case-sheet, and analytics routes.
3. Test PgBouncer transaction pooling with `SET LOCAL`/RLS before adopting it. Add a separate read-only analytics engine and lag-aware behavior.
4. Partition high-volume `audit_log` and `interview_turns` only after migration rehearsals and retention requirements are approved.
5. Scale API replicas behind a TLS reverse proxy, then scale workers according to outbox age and queue processing delay. Use managed PostgreSQL/Redis with tested restore procedures for multi-clinic deployments.
6. Add per-clinic dashboards and alerts for database pool saturation, queue update delay, outbox age, and Redis memory pressure.

The local Compose stack and its smoke tests are development tools. They do not establish a production capacity, recovery objective, or security certification.
