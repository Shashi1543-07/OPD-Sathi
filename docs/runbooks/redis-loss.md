# Runbook: Redis state lost or corrupted

**Alerts:** `RedisDown`, often with `QueueUpdatesStuck` · **Owner:** hospital IT ·
**Time:** 10 minutes · **Data at risk:** none clinical (task 7.7.4)

## What Redis holds, and what losing it costs

PostgreSQL is the record of every visit, answer, case sheet and queue position. Redis holds
working state only:

| In Redis | If lost |
|---|---|
| Department queues (sorted sets) | **Rebuilt from PostgreSQL** automatically at start-up, or with `queue-rebuild` |
| Interviews still in progress (before the patient confirms) | Those patients start their check-in again; confirmed visits are safe |
| Idempotency keys | A staff action retried within seconds of the loss could apply twice; every action is checked against the visit's current state, so a repeat is refused |
| Rate-limit counters | Reset to zero (briefly more permissive) |
| Status-page cache, live-board pub/sub | Reconnect by themselves within seconds |

Redis writes to disk every second (`appendfsync everysec`), so a crash loses at most about one
second of this state.

## Steps

1. See what happened:

   ```sh
   cd /opt/opd-saathi
   C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
   $C ps redis
   $C logs --tail 100 redis
   ```

2. If the container stopped, start it: `$C up -d redis`. If its append-only file is corrupt
   (the log says so), move the data aside and start fresh:

   ```sh
   $C stop redis
   docker run --rm -v opd-saathi_redis_data:/data alpine sh -c 'mkdir -p /data/corrupt-$(date +%s) && mv /data/appendonly* /data/dump.rdb /data/corrupt-*/ 2>/dev/null; true'
   $C up -d redis
   ```

3. Rebuild the queues from PostgreSQL (the worker also does this at start-up):

   ```sh
   $C run --rm api python -m app.cli queue-rebuild
   $C restart worker api
   ```

4. Check: Staff → Queue shows the same patients in the same order as before; `/api/readyz`
   answers `ready`.

5. Tell the OPD desk: patients who were in the middle of checking in at the moment of the loss
   start again at the kiosk; anyone who already has a token is safe.

## Drill

Held before go-live and after any change to the queue code, on staging with a simulated OPD
(`scripts/chaos_isolated.py --confirm-isolated`): delete Redis data mid-session and confirm
every queued patient reappears in order and no confirmed visit is lost.
