# Runbook: what each alert means and what to do

**Owner:** hospital IT, with second-line support · **Where alerts come from:** Prometheus and
Alertmanager on the server (ADR 010; rules in `infra/monitoring/alerts.yml`) · **Who receives
them:** the address and Telegram group set at installation (`/etc/opd-saathi/alertmanager.yml`)

Critical alerts repeat every hour until fixed; warnings every four hours. Each message says
which alert fired and which runbook to open. Run commands from `/opt/opd-saathi` with
`C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"`.

To see what Prometheus sees, open an SSH tunnel from your PC and browse to
`http://localhost:9090/alerts`:

```sh
ssh -L 9090:127.0.0.1:9090 admin@<server>
```

## Availability

| Alert | Meaning | First step |
|---|---|---|
| `ApiDown` | The API has not answered Prometheus for 2 minutes. Kiosks and staff screens fail. | `server-down.md` |
| `WebDown` | nginx (HTTPS) is not answering. Nothing reaches the server. | `server-down.md` |
| `WorkerDown` | The worker is down: live boards stop updating, SMS stop, the night close-out does not run. Check-in still works. | `$C ps worker`, `$C logs --tail 100 worker`, `$C up -d worker` |
| `DatabaseDown` | The worker cannot reach PostgreSQL. | `database-down.md` |
| `RedisDown` | The worker cannot reach Redis. Queues and sign-in are affected. | `redis-loss.md` |

## Service quality

| Alert | Meaning | First step |
|---|---|---|
| `ApiErrorRateHigh` | Over 2% of API requests fail for 5 minutes. | `$C logs --since 15m api \| grep -i error`; look for a common route or dependency; check `DatabaseDown`/`RedisDown` first |
| `ApiSlow` | The slowest 5% of requests take over 1.5 s for 10 minutes. | Check CPU and memory (`docker stats`), the disk (`df -h`), and whether a report export or backup is running; tell second line if it persists |
| `WorkerJobFailing` | A worker job (named in the alert) failed more than 3 times in 10 minutes. | `$C logs --since 15m worker`; the log names the job and error type (never patient data) |
| `QueueUpdatesStuck` | A queue change has waited over 30 s to reach the live boards. Boards and status pages show stale positions. | Usually the worker: see `WorkerDown`; then `$C exec api python -m app.cli queue-rebuild` |
| `SmsBacklog` | More than 50 queue messages unsent for 15 minutes. | The SMS provider or its credentials: `$C logs --since 30m worker \| grep notification` |

## Patient safety

| Alert | Meaning | First step |
|---|---|---|
| `UrgentAlertUnacknowledged` | A patient tapped a danger sign (or the rules flagged an emergency) and no nurse has acknowledged it for 5 minutes. **A person may be seriously unwell in the waiting area.** | Phone the OPD nurse station now; ask them to open Alerts on the board and go to the patient. Then find out why the board did not alert (screen off, muted, board not open) |
| `KioskSilent` | A paired kiosk has not checked in for 10 minutes during OPD hours (08:00–14:00 IST). | Walk to the kiosk: power, network, browser closed. Settings → Kiosks shows its last check-in |

## Server

| Alert | Meaning | First step |
|---|---|---|
| `DiskSpaceLow` / `DiskSpaceCritical` | The data disk is over 80% / 90% full. | `disk-full.md` |
| `BackupMissing` | No successful backup for 26 hours. | `restore.md`, "Backups failing" |
| `BackupNeverReported` | The backup job has never reported success to monitoring (new installation, or the status volume is not mounted). | `$C logs backup`; run a backup now |
| `CertificateExpiring` | The HTTPS certificate expires within 21 days. After expiry every browser refuses the site. | `certificate.md` |

`AlertingSelfCheck` always fires and is routed nowhere; it shows in Prometheus so you can see
the alert pipeline is running.

## Drill (before go-live, then twice a year)

Every alert must reach a person once (`docs/GO_LIVE_CHECKLIST.md`). On staging, outside OPD
hours, and record the time each message arrived:

1. `ApiDown`, `WorkerDown`, `WebDown`: `$C stop api` (then `worker`, `web`) for 3 minutes; start again.
2. `DatabaseDown`, `RedisDown`: `$C stop postgres` (then `redis`) for 2 minutes; start again.
3. `UrgentAlertUnacknowledged`: check in a synthetic patient on a kiosk with a danger sign and
   leave the alert for 6 minutes.
4. `KioskSilent`: close a paired kiosk's browser during OPD hours for 11 minutes.
5. The rest are proven by the rule tests in CI (`infra/monitoring/alerts.test.yml`) and by
   sending a test message: `$C exec alertmanager amtool alert add DrillTest severity=critical
   --annotation=summary="Drill: alerts reach you" --alertmanager.url=http://localhost:9093`.
