# Runbook: disk filling up

**Alerts:** `DiskSpaceLow` (over 80%), `DiskSpaceCritical` (over 90%) · **Owner:** hospital IT ·
**Time:** 30 minutes

## Why it matters

When the disk fills, PostgreSQL stops accepting writes: check-ins, calls and case sheets fail.
Redis stops persisting its state. Backups fail.

## Find what is using it

```sh
df -h /var/lib/docker /var/lib/pgbackrest 2>/dev/null
sudo du -xh --max-depth=2 /var/lib/docker | sort -h | tail -15
docker system df
```

Usual causes, most likely first:

1. **Old images and build cache** after upgrades: `docker image prune -a --filter "until=720h"`
   and `docker builder prune --filter "until=720h"`. Never prune volumes.
2. **Backups kept locally longer than planned**: check the retention in
   `/etc/pgbackrest/pgbackrest.conf` (`repo1-retention-full`) and run
   `docker compose ... exec backup pgbackrest --stanza=osa expire`. Confirm the off-site copy is
   current before removing anything local.
3. **Photo growth** (old papers): see the sizing in `docs/SCALING.md`; the retention job removes
   photos past the approved period (`docs/PRIVACY.md`).
4. **Container logs**: production rotates them (20 MB × 10 per container); if a container was
   started without the overlay, restart it with it.
5. **WAL piling up** because archiving fails: `docker compose ... exec backup pgbackrest
   --stanza=osa check`; fix the backup repository (off-site link, credentials) and the WAL drains.

## If writes have already stopped

1. Switch the OPD to paper (`server-down.md`).
2. Free space with step 1 (images and build cache) first: it is always safe.
3. Restart PostgreSQL if it shut down: `docker compose ... up -d postgres`, then the rest.
4. Plan more disk with the hospital: the sizing guide is in `docs/DEPLOYMENT.md`.

Never delete files inside PostgreSQL's data directory or `pg_wal` by hand.
