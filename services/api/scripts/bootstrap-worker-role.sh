#!/bin/sh
set -eu

# A password may come from <NAME>_FILE (production mounts secrets as files, task 7.3.7).
from_file() {
  eval "file=\${${1}_FILE:-}"
  if [ -n "$file" ]; then
    value=$(cat "$file")
    export "$1=$value"
  fi
}
for name in POSTGRES_ADMIN_PASSWORD OSA_WORKER_PASSWORD; do from_file "$name"; done
: "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD must be set}"
: "${OSA_WORKER_PASSWORD:?OSA_WORKER_PASSWORD must be set}"

export PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"
psql --host "$POSTGRES_HOST" --username "$POSTGRES_ADMIN_USER" --dbname "$POSTGRES_DB" \
  --set=worker_password="$OSA_WORKER_PASSWORD" \
  --set=database_name="$POSTGRES_DB" <<'SQL'
SELECT format(
  'CREATE ROLE osa_worker LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS',
  :'worker_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_worker')
\gexec
ALTER ROLE osa_worker LOGIN PASSWORD :'worker_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS;
SELECT 'CREATE ROLE osa_analytics_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'osa_analytics_worker')
\gexec
GRANT CONNECT ON DATABASE :"database_name" TO osa_worker;
GRANT osa_worker TO osa_migrator;
GRANT osa_analytics_worker TO osa_migrator, osa_worker;
CREATE SCHEMA IF NOT EXISTS osa_analytics AUTHORIZATION osa_analytics_worker;
ALTER SCHEMA osa_analytics OWNER TO osa_analytics_worker;
SQL
