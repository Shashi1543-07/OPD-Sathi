#!/bin/sh
set -eu

archive_dir=${1:?Usage: RESTORE_DATABASE=osa_restore_name scripts/restore.sh <backup-directory>}
restore_database=${RESTORE_DATABASE:-}
configured_database=${POSTGRES_DB:-osa}

case "$restore_database" in
  osa_restore_*) ;;
  *) echo "RESTORE_DATABASE must start with osa_restore_" >&2; exit 2 ;;
esac
if [ "$restore_database" = "$configured_database" ]; then
  echo "Refusing to restore over the configured application database." >&2
  exit 2
fi
if [ ! -f "$archive_dir/postgres.dump" ] || [ ! -f "$archive_dir/redis-state.rdb" ]; then
  echo "Backup directory is missing a required snapshot." >&2
  exit 2
fi
(cd "$archive_dir" && sha256sum --check CHECKSUMS.sha256)

docker compose exec -T -e RESTORE_DATABASE="$restore_database" postgres sh -c \
  'createdb --username="$POSTGRES_USER" --template=template0 "$RESTORE_DATABASE"'
docker compose exec -T -e RESTORE_DATABASE="$restore_database" postgres sh -c \
  'pg_restore --exit-on-error --no-owner --no-acl --username="$POSTGRES_USER" --dbname="$RESTORE_DATABASE"' \
  < "$archive_dir/postgres.dump"

expected_visits=$(cat "$archive_dir/visits.count")
case "$expected_visits" in
  ''|*[!0-9]*) echo "Backup visit-count manifest is invalid." >&2; exit 2 ;;
esac
restored_visits=$(docker compose exec -T -e RESTORE_DATABASE="$restore_database" postgres sh -c \
  'psql --no-psqlrc --tuples-only --no-align --username="$POSTGRES_USER" --dbname="$RESTORE_DATABASE" --command="SELECT count(*) FROM visits"' \
  | tr -d '[:space:]')
if [ "$restored_visits" != "$expected_visits" ]; then
  echo "Restored visit count does not match the backup manifest." >&2
  echo "expected=$expected_visits restored=$restored_visits" >&2
  exit 1
fi

printf 'PostgreSQL restored to isolated database: %s\n' "$restore_database"
printf 'Visit rows verified: %s\n' "$restored_visits"
printf 'Redis RDB snapshot verified but not loaded; restore it only into an isolated Redis instance.\n'
