# Runbook: backups and restoring the database

**Alerts:** `BackupMissing`, `BackupNeverReported` · **Owner:** hospital IT with second-line
support · **Targets (task 7.7.1, to be signed by the hospital):** lose at most 5 minutes of
data; back in service within 4 hours on one server

Run commands from `/opt/opd-saathi` with
`C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"`.

## How backups work

- PostgreSQL archives every write-ahead log (WAL) segment to the backup repository as soon as it
  is complete, and closes a segment at least every 5 minutes (ADR 011).
- The `backup` service takes a **full** backup on the night into Sunday and a **differential**
  backup every other night, at 01:30 IST. Two full backups are kept: any minute of the last
  8 to 14 days can be restored.
- Everything in the repository is encrypted (AES-256) with `secrets/pgbackrest_cipher_pass`.
  That passphrase is escrowed with the data keys (`key-escrow.md`). **Without it the backups
  cannot be read; without `app_data_key` the restored records cannot be read.**
- The repository is the volume `pgbackrest-repo` on this server and, when configured, a second
  copy off-site (repo2: another building's NAS or an S3-compatible bucket in India;
  `docs/DEPLOYMENT.md`, "Backups").
- After every successful backup the worker reports its time to monitoring; no success for
  26 hours raises `BackupMissing`.

Check the backups at any time:

```sh
$C exec backup with-backup-secrets pgbackrest --stanza=osa info
```

## Backups failing (`BackupMissing`)

```sh
$C logs --tail 100 backup
$C exec backup with-backup-secrets pgbackrest --stanza=osa check
```

| What you see | Do |
|---|---|
| "No space left on device" | `disk-full.md`; then `$C exec backup backup-loop run full` |
| "unable to find a valid repository" or cipher errors | `secrets/pgbackrest_cipher_pass` missing or changed: restore it from escrow, never replace it |
| repo2 (off-site) unreachable | Network or credentials; local backups continue. Fix within a day: the off-site copy is the one that survives losing the server |
| "archive command failed" in the PostgreSQL log | WAL waits on the server until archiving works again; watch the disk (`df -h`) |

Take a backup by hand (for example before an upgrade; `setup-hospital.sh` does this):

```sh
$C exec backup backup-loop run full
```

## Restore the live database to a point in time

For data damaged by a mistake, a faulty upgrade or corruption. **Everything written after the
chosen time is lost**; the OPD works on paper meanwhile (`server-down.md`). Agree the time with
the clinical lead and write it down first.

1. Stop everything that writes, then the database:

   ```sh
   $C stop web api worker backup
   $C stop postgres
   ```

2. Restore in place to the chosen time (IST shown here), keeping files that already match:

   ```sh
   $C run --rm --no-deps postgres pgbackrest --stanza=osa --delta \
     --type=time --target="2026-11-20 10:42:00+05:30" --target-action=promote restore
   ```

   To go back to the last moment before a known event instead, use the latest backup with
   `--type=default` (replays every archived segment).

3. Start the database and check it:

   ```sh
   $C up -d postgres
   $C logs --tail 50 postgres        # "database system is ready to accept connections"
   $C exec postgres psql -U postgres -d osa -c "select max(created_at) from visits"
   ```

4. Start the rest, rebuild the live queues from the restored records, and take a new full
   backup at once (the restored database starts a new history; older backups stay usable):

   ```sh
   $C up -d api worker web backup
   $C exec api python -m app.cli queue-rebuild
   $C exec backup backup-loop run full
   ```

5. Check every encrypted value reads: `$C exec api python -m app.cli verify-decrypt`.
6. Record the incident (`docs/INCIDENT_RESPONSE.md`): chosen time, data lost, who decided.

## The server is lost

1. Install the replacement with `setup-hospital.sh`, **with the escrowed secrets copied into
   `/etc/opd-saathi/secrets` first** (the data keys and `pgbackrest_cipher_pass`), and the same
   repo2 settings in `.env`. Do not onboard a hospital.
2. Stop everything (`$C stop`), restore from the off-site repository, then continue at step 3
   above:

   ```sh
   $C run --rm --no-deps postgres pgbackrest --stanza=osa --repo=2 --delta restore
   ```

## Restore drill

`scripts/restore-drill.sh` restores the backups to a throwaway database without touching the
live one, compares every table's row count and decrypts every encrypted value with the keys in
the ring. It runs in CI on every change (production smoke test). On the real server run it
**every quarter**, outside OPD hours, and record it here and in the go-live checklist:

```sh
sudo scripts/restore-drill.sh
```

| Date | Run by | Backup restored | Result | Signed |
|---|---|---|---|---|
| | | | | |

Once a year, also restore from the **off-site** copy onto a spare machine (the "server is lost"
procedure) and time it against the 4-hour target.
