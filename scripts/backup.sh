#!/bin/sh
set -eu

backup_root=${BACKUP_DIR:-./backups}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive_dir="$backup_root/$timestamp"
umask 077
mkdir -p "$archive_dir"
chmod 700 "$archive_dir"

docker compose exec -T postgres sh -c \
  'pg_dump --format=custom --no-owner --no-acl --username="$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$archive_dir/postgres.dump"

docker compose exec -T redis sh -c \
  'redis-cli -a "$REDIS_PASSWORD" --rdb /tmp/osa-state.rdb >/dev/null 2>&1 && cat /tmp/osa-state.rdb && rm -f /tmp/osa-state.rdb' \
  > "$archive_dir/redis-state.rdb"

docker compose exec -T postgres sh -c \
  'psql --no-psqlrc --tuples-only --no-align --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT count(*) FROM visits"' \
  | tr -d '[:space:]' > "$archive_dir/visits.count"

(
  cd "$archive_dir"
  sha256sum postgres.dump redis-state.rdb visits.count > CHECKSUMS.sha256
)
chmod 600 "$archive_dir"/*
printf 'Backup created: %s\n' "$archive_dir"
