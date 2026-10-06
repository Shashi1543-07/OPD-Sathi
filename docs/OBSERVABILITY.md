# Local observability

The optional Compose profile runs Prometheus and Grafana on loopback only. It
collects API request counts and latency by HTTP method, FastAPI route template,
and status code. It does not label metrics with request IDs, tenant IDs, patient
IDs, query strings, or request contents.

## Start it

1. Set a unique `GRAFANA_ADMIN_PASSWORD` in `.env`.
2. Set `METRICS_ENABLED=true` in `.env`. The API returns 404 for `/metrics` by
   default; when enabled, access is intended only over the private Compose
   network. Keep the API bound to loopback or behind a private authenticated
   gateway. Do not expose `/metrics` publicly.
3. Run `docker compose --profile observability up -d --build api prometheus grafana`.
4. Open Grafana at `http://localhost:3000` and sign in with the configured admin
   credentials. The `OPD Saathi API` dashboard and Prometheus data source are
   provisioned automatically. Prometheus is at `http://localhost:9090`.

The dashboard includes request rate, HTTP p95 latency, and 5xx rate. Alert rules
fire when 5xx rate exceeds 2% or API HTTP p95 exceeds 1.5 seconds for five
minutes. These are local evaluation alerts; no notification channel is
configured.

## Limits

The exporter uses fixed histogram buckets and bounded route templates, but its
counters live in each API worker's memory. With multiple Gunicorn workers,
successive scrapes can observe different worker snapshots; use a Prometheus
multiprocess exporter or a single metrics aggregation service before relying on
these values for production SLOs. Provider latency, queue depth, active WebSocket
connections, outbox age, alert delivery, and OpenTelemetry traces are not yet
exported. The profile therefore provides a local HTTP dashboard, not the full
Phase 13 observability gate.
