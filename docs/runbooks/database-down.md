# Runbook: PostgreSQL down or not accepting connections

**Alerts:** `DatabaseDown`, often with `ApiErrorRateHigh` and `WorkerJobFailing` ·
**Owner:** hospital IT · **Time:** 15 minutes to diagnose

## Why it matters

PostgreSQL holds every record. While it is down nothing can be checked in, called or read.
Switch the OPD to paper at once if it is not back within 5 minutes (`server-down.md`).

## Diagnose

```sh
cd /opt/opd-saathi
C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$C ps postgres
$C logs --tail 200 postgres
$C exec postgres pg_isready
df -h /var/lib/docker
```

| What you see | Do |
|---|---|
| Container exited, "No space left on device" | `disk-full.md` |
| "too many connections" | Count: `$C exec postgres psql -U postgres -c "select usename, state, count(*) from pg_stat_activity group by 1,2"`. Restart the API (`$C restart api`) to release a leaked pool; check the connection budget (`docs/SCALING.md`) |
| "database system is in recovery mode" after a crash | Wait: it replays its log and comes back by itself; watch the log |
| "could not open file" or checksum errors | Data corruption: stop, do not restart repeatedly; restore to the last good point (`restore.md`) |
| Container keeps restarting with permission errors | The volume's ownership changed; never `chmod` by hand; contact second-line support |

## After it is back

1. `curl -fsS https://<host>/api/readyz` answers `ready`.
2. The worker catches up its outbox by itself; check Staff → Queue.
3. Enter paper visits if the OPD switched to paper (`server-down.md`).
4. Record the outage (`docs/INCIDENT_RESPONSE.md`).
