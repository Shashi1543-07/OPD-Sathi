#!/bin/sh
set -eu

: "${OSA_APP_PASSWORD:?OSA_APP_PASSWORD must be set}"
: "${OSA_MIGRATOR_PASSWORD:?OSA_MIGRATOR_PASSWORD must be set}"
: "${OSA_WORKER_PASSWORD:?OSA_WORKER_PASSWORD must be set}"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$OSA_APP_PASSWORD" \
  --set=migrator_password="$OSA_MIGRATOR_PASSWORD" \
  --set=worker_password="$OSA_WORKER_PASSWORD" \
  --set=database_name="$POSTGRES_DB" <<'SQL'
CREATE ROLE osa_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE :"database_name" TO osa_app;
CREATE ROLE osa_migrator LOGIN PASSWORD :'migrator_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE :"database_name" TO osa_migrator;
GRANT CREATE ON DATABASE :"database_name" TO osa_migrator;
GRANT CREATE ON SCHEMA public TO osa_migrator;
CREATE ROLE osa_worker LOGIN PASSWORD :'worker_password' NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
GRANT CONNECT ON DATABASE :"database_name" TO osa_worker;
GRANT osa_worker TO osa_migrator;
CREATE ROLE osa_analytics_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
GRANT osa_analytics_worker TO osa_migrator, osa_worker;
CREATE SCHEMA osa_analytics AUTHORIZATION osa_analytics_worker;
SQL
