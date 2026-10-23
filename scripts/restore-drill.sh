#!/usr/bin/env bash
# Restore drill (Phase 7, task 7.7.5; docs/runbooks/restore.md): prove the backups restore.
#
#   sudo scripts/restore-drill.sh            # on the server, outside OPD hours
#   OSA_COMPOSE="docker compose -p ... -f ..." scripts/restore-drill.sh   # another stack
#
# Never touches the live database. It:
#   1. counts the rows of every table in the live database and notes the time;
#   2. restores the backup repository to that exact point into a throwaway volume;
#   3. starts a second PostgreSQL on it (its own port and socket, WAL archiving off);
#   4. compares every table's row count with step 1;
#   5. decrypts every encrypted value of the restored copy with the keys in the ring
#      (`python -m app.cli verify-decrypt`): a restore is only useful if the keys work too;
#   6. removes the throwaway database and volume.
# Exit status 0 only when every step passed. Write the result in the drill log (restore.md).

set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
read -r -a C <<<"${OSA_COMPOSE:-docker compose -f docker-compose.yml -f docker-compose.prod.yml}"
STAMP="$(date -u +%Y%m%d%H%M%S)"
VOLUME="osa-restore-drill-$STAMP"
DRILL="osa-restore-drill-$STAMP"
DB="${POSTGRES_DB:-osa}"
WORK="$(mktemp -d)"

cleanup() {
  docker rm -f "$DRILL" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }

# Exact row counts for every table, as the superuser (row-level security does not apply).
COUNTS_SQL="SELECT table_schema || '.' || table_name || '|' || (xpath('/row/c/text()',
  query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),
  false, true, '')))[1]::text FROM information_schema.tables
  WHERE table_type = 'BASE TABLE' AND table_schema IN ('public', 'osa_analytics')
  ORDER BY 1"

step "Counting rows in the live database"
"${C[@]}" exec -T postgres psql -U postgres -d "$DB" -qAt -c "$COUNTS_SQL" > "$WORK/live.txt"
echo "  $(wc -l < "$WORK/live.txt") tables"
# A named restore point right after counting: recovery stops exactly there (a time target can
# overshoot the end of the archive on a quiet database, where no transaction follows it).
TARGET="drill-$STAMP"
"${C[@]}" exec -T postgres psql -U postgres -d "$DB" -qAt -c \
  "SELECT pg_create_restore_point('$TARGET')" >/dev/null
# Close the current WAL segment so the restore point is in the archive, and wait for it.
"${C[@]}" exec -T postgres psql -U postgres -d "$DB" -qAt -c "SELECT pg_switch_wal()" >/dev/null
"${C[@]}" exec -T backup with-backup-secrets pgbackrest --stanza=osa check
echo "  restoring to restore point $TARGET"

step "Restoring into a throwaway volume"
docker volume create "$VOLUME" >/dev/null
# The image runs as postgres; an empty volume mounted at /var/lib/postgresql/restore takes the
# owner of that directory in the image, so no root step is needed.
RESTORED=/var/lib/postgresql/restore
"${C[@]}" run --rm --no-deps -T -v "$VOLUME:$RESTORED" postgres \
  pgbackrest --stanza=osa --pg1-path="$RESTORED" --type=name --target="$TARGET" \
  --target-action=promote restore

step "Starting PostgreSQL on the restored copy"
# Port 5433 and a private socket directory: nothing can mistake it for the live database.
"${C[@]}" run -d --no-deps --name "$DRILL" -v "$VOLUME:$RESTORED" -e PGDATA="$RESTORED" postgres \
  postgres -c archive_mode=off -c port=5433 -c unix_socket_directories=/tmp >/dev/null
drill_sql() { docker exec "$DRILL" psql -h /tmp -p 5433 -U postgres -d "$DB" -qAt -c "$1"; }
for _ in $(seq 1 120); do
  [[ "$(drill_sql 'SELECT pg_is_in_recovery()' 2>/dev/null || true)" == "f" ]] && break
  sleep 2
done
[[ "$(drill_sql 'SELECT pg_is_in_recovery()')" == "f" ]] ||
  { docker logs --tail 30 "$DRILL"; echo "FAIL the restored database did not finish recovery"; exit 1; }
echo "  recovered and promoted"

step "Comparing row counts"
drill_sql "$COUNTS_SQL" > "$WORK/restored.txt"
if diff -u "$WORK/live.txt" "$WORK/restored.txt"; then
  echo "ok   all $(wc -l < "$WORK/live.txt") tables have the same row counts"
else
  echo "FAIL row counts differ (above). If OPD was running, repeat outside OPD hours."
  exit 1
fi

step "Decrypting every encrypted value of the restored copy"
"${C[@]}" run --rm --no-deps -T \
  -e MIGRATIONS_DATABASE_URL_FILE= -e WORKER_DATABASE_URL_FILE= -e DATABASE_URL_FILE= \
  --entrypoint sh api -c "
    to_drill() { sed 's|@postgres:5432/|@$DRILL:5433/|' \"/run/secrets/\$1\"; }
    export MIGRATIONS_DATABASE_URL=\"\$(to_drill migrations_database_url)\"
    export WORKER_DATABASE_URL=\"\$(to_drill worker_database_url)\"
    export DATABASE_URL=\"\$(to_drill database_url)\"
    exec python -m app.cli verify-decrypt"

echo
echo "Restore drill passed: backup restored to restore point $TARGET, every table complete, every value readable."
