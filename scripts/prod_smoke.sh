#!/usr/bin/env bash
# Production smoke test (Phase 7, task 7.1.2): start the stack exactly as a hospital runs it
# (docker-compose.prod.yml, secrets as files, HTTPS) with throwaway test secrets, then drive it.
#
#   scripts/prod_smoke.sh          # build, run, check, tear down
#   KEEP=1 scripts/prod_smoke.sh   # leave the stack running afterwards (project osa-smoke)
#
# Needs Docker with Compose, openssl and a Python with httpx and pyotp (the API's lock file).
# Synthetic data only. Uses ports 8443/8080 (SMOKE_HTTPS_PORT, SMOKE_HTTP_PORT) so it never
# collides with a running installation.

set -euo pipefail
# Git Bash on Windows would rewrite container paths such as /app/alembic.ini.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

PROJECT=osa-smoke
WORK="$(mktemp -d)"
SECRETS="$WORK/secrets"
CERTS="$WORK/certs"
mkdir -p "$SECRETS" "$CERTS"
PYTHON="${PYTHON:-python}"
HTTPS_PORT="${SMOKE_HTTPS_PORT:-8443}"
HTTP_PORT="${SMOKE_HTTP_PORT:-8080}"
export SMOKE_HTTP_URL="http://localhost:$HTTP_PORT"

# Strip \r too: openssl on Windows ends its output with CRLF.
hex() { openssl rand -hex 32 | tr -d '\r\n'; }
b64() { openssl rand -base64 32 | tr -d '\r\n'; }
put() { printf '%s' "$2" > "$SECRETS/$1"; chmod 0644 "$SECRETS/$1"; }

for name in postgres_password osa_app_password osa_migrator_password osa_worker_password \
  redis_password jwt_secret pgbackrest_cipher_pass; do
  put "$name" "$(hex)"
done
for name in app_data_key app_hmac_key cursor_hmac_key notify_key; do put "$name" "$(b64)"; done
for name in gemini_api_key sarvam_api_key twilio_auth_token whatsapp_access_token smtp_password \
  telegram_bot_token pgbackrest_repo2_cipher_pass pgbackrest_repo2_s3_key \
  pgbackrest_repo2_s3_key_secret; do put "$name" ""; done
read_secret() { cat "$SECRETS/$1"; }
put database_url "postgresql+asyncpg://osa_app:$(read_secret osa_app_password)@postgres:5432/osa"
put worker_database_url "postgresql+asyncpg://osa_worker:$(read_secret osa_worker_password)@postgres:5432/osa"
put migrations_database_url "postgresql+asyncpg://osa_migrator:$(read_secret osa_migrator_password)@postgres:5432/osa"
put redis_state_url "redis://:$(read_secret redis_password)@redis:6379/0"
put redis_cache_url "redis://:$(read_secret redis_password)@redis-cache:6379/0"

# Native Windows tools (Docker Desktop, Git's openssl) need C:/... paths, not /tmp/...
host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes -keyout "$(host_path "$CERTS/tls.key")" \
  -out "$(host_path "$CERTS/tls.crt")" -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost" >/dev/null 2>&1
chmod 0644 "$CERTS/tls.crt" "$CERTS/tls.key"
# Alerts go nowhere in the smoke test; the configuration must still be valid.
ALERT_EMAIL=it@example.test SMTP_FROM=opd-saathi@example.test SMTP_HOST=localhost:25 \
  bash scripts/alertmanager-config.sh "$WORK/alertmanager.yml"

cat > "$WORK/.env" <<ENV
POSTGRES_DB=osa
POSTGRES_USER=postgres
WEB_ORIGIN=https://localhost:$HTTPS_PORT
PUBLIC_WEB_URL=https://localhost:$HTTPS_PORT
LLM_ENABLED=false
OSA_SECRETS_DIR=$(host_path "$SECRETS")
OSA_CERTS_DIR=$(host_path "$CERTS")
OSA_SUBNET=172.31.250.0/24
WEB_PROXY_IP=172.31.250.10
OSA_ALERTMANAGER_CONFIG=$(host_path "$WORK/alertmanager.yml")
# Production logs to journald; Docker Desktop has none, CI's runner does.
OSA_LOG_DRIVER=${OSA_LOG_DRIVER:-json-file}
ENV
# Smoke-test ports only; a hospital install publishes 443 and 80.
cat > "$WORK/ports.yml" <<YAML
services:
  web:
    ports: !override
      - "127.0.0.1:$HTTPS_PORT:8443"
      - "127.0.0.1:$HTTP_PORT:8080"
  prometheus:
    ports: !reset []
YAML

C=(docker compose -p "$PROJECT" --env-file "$(host_path "$WORK/.env")" -f docker-compose.yml \
  -f docker-compose.prod.yml -f "$(host_path "$WORK/ports.yml")")
cleanup() {
  if [[ "${KEEP:-0}" != "1" ]]; then
    "${C[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK"
  else
    echo "Stack left running: docker compose -p $PROJECT ... (secrets in $WORK)"
    # For scripts that continue with the running stack (scripts/load-test.sh).
    [[ -n "${SMOKE_COMPOSE_FILE:-}" ]] && printf '%s\n' "${C[*]}" > "$SMOKE_COMPOSE_FILE"
  fi
}
trap cleanup EXIT

echo "==> Building and starting the production stack"
# A stack left by an earlier KEEP=1 run has a database made with other passwords; start clean.
"${C[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
"${C[@]}" build --quiet api worker web postgres backup worker-role-bootstrap
"${C[@]}" up -d postgres redis redis-cache worker-role-bootstrap
"${C[@]}" run --rm api alembic -c /app/alembic.ini upgrade head
"${C[@]}" up -d api worker web prometheus alertmanager backup

echo "==> Refusing development values"
# The stack must not start in production with a development signing key.
refusal="$("${C[@]}" run --rm --no-deps -e JWT_SECRET_FILE= \
  -e JWT_SECRET=development-only-signing-secret-change-before-deploy \
  api python -c "from app.config import Settings; Settings()" 2>&1 || true)"
if grep -q "production JWT_SECRET must contain at least 32 random bytes" <<<"$refusal"; then
  echo "ok   production refuses a development signing key"
else
  echo "FAIL production did not refuse a development signing key"; echo "$refusal" | tail -5; exit 1
fi

echo "==> Onboarding a synthetic hospital"
ADMIN_EMAIL="smoke-admin@example.test"
ADMIN_PASSWORD="saffron kettle orbit 2468"
printf '%s\n' "$ADMIN_PASSWORD" | "${C[@]}" run --rm -T api python -m app.cli setup-hospital \
  --name "Smoke Test Hospital" --code "smoke-$(date +%s)" --admin-name "Smoke Administrator" \
  --admin-email "$ADMIN_EMAIL" --admin-password-stdin

echo "==> Waiting for the stack to be healthy"
for _ in $(seq 1 60); do
  if curl -fsSk https://localhost:$HTTPS_PORT/api/readyz >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "==> Driving it through HTTPS"
"$PYTHON" scripts/prod_smoke.py "https://localhost:$HTTPS_PORT" "$ADMIN_EMAIL" "$ADMIN_PASSWORD"

echo "==> No secret in docker inspect or the process list"
leaks=0
for container in $("${C[@]}" ps -q); do
  inspect="$(docker inspect "$container")"
  processes="$(docker top "$container" -eo args 2>/dev/null || docker top "$container")"
  for file in "$SECRETS"/*; do
    value="$(cat "$file")"
    [[ ${#value} -lt 16 ]] && continue
    if grep -qF -- "$value" <<<"$inspect$processes"; then
      echo "FAIL $(basename "$file") visible in $(docker inspect -f '{{.Name}}' "$container")"
      leaks=1
    fi
  done
done
[[ $leaks -eq 0 ]] && echo "ok   no secret visible in any container's environment or process list"

echo "==> Health checks"
for service in worker web api; do
  status=starting
  for _ in $(seq 1 45); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$("${C[@]}" ps -q "$service")")"
    [[ "$status" == "starting" ]] || break
    sleep 2
  done
  [[ "$status" == "healthy" ]] && echo "ok   $service healthy" || { echo "FAIL $service $status"; leaks=1; }
done

echo "==> Monitoring"
# Prometheus scrapes every part of the stack and the worker reports its alert gauges.
prom() { "${C[@]}" exec -T prometheus wget -qO- "http://localhost:9090/api/v1/$1" 2>/dev/null; }
query() { "${C[@]}" exec -T prometheus wget -qO- --post-data "query=$1" "http://localhost:9090/api/v1/query" 2>/dev/null; }
for _ in $(seq 1 30); do
  targets="$(prom targets || true)"
  [[ "$(grep -o '"health":"up"' <<<"$targets" | wc -l)" -ge 5 ]] && break
  sleep 2
done
for job in api worker web prometheus alertmanager; do
  if "$PYTHON" -c "
import json, sys
targets = json.loads(sys.stdin.read())['data']['activeTargets']
sys.exit(0 if any(t['labels']['job'] == '$job' and t['health'] == 'up' for t in targets) else 1)
" <<<"$targets"; then echo "ok   Prometheus scrapes $job"; else echo "FAIL Prometheus cannot scrape $job"; leaks=1; fi
done
for _ in $(seq 1 30); do
  gauges="$(query 'data_disk_free_ratio{job="worker"}' || true)"
  grep -q '"value"' <<<"$gauges" && break
  sleep 2
done
# The API must not export worker-only gauges (they would read 0 and fire false alerts).
if query 'data_disk_free_ratio{job="api"}' | grep -q '"value"'; then
  echo "FAIL the API exports worker-only gauges"; leaks=1
else
  echo "ok   worker-only gauges come from the worker alone"
fi
for metric in 'dependency_up{job="worker",dependency="postgres"}' \
  'dependency_up{job="worker",dependency="redis"}' 'data_disk_free_ratio{job="worker"}' \
  'tls_certificate_expiry_timestamp_seconds{job="worker"}'; do
  raw="$(query "$metric" || true)"
  value="$("$PYTHON" -c "
import json, sys
try:
    result = json.loads(sys.stdin.read())['data']['result']
except ValueError:
    result = []
print(result[0]['value'][1] if result else '')
" <<<"$raw")"
  if [[ -n "$value" && "$value" != "0" ]]; then echo "ok   worker reports $metric"; else
    echo "FAIL worker does not report $metric: ${raw:0:300}"; leaks=1; fi
done
echo "==> Backups"
# The backup service takes a first full backup at once; WAL archiving runs from the start.
for _ in $(seq 1 60); do
  info="$("${C[@]}" exec -T backup with-backup-secrets pgbackrest --stanza=osa --output=json info 2>/dev/null || true)"
  grep -q '"type":"full"' <<<"$info" && break
  sleep 3
done
if grep -q '"type":"full"' <<<"$info"; then echo "ok   first full backup in the encrypted repository"; else
  echo "FAIL no full backup: $("${C[@]}" logs --tail 20 backup 2>&1)"; leaks=1; fi
grep -q '"cipher":"aes-256-cbc"' <<<"$info" && echo "ok   backup repository encrypted" ||
  { echo "FAIL backup repository not encrypted"; leaks=1; }
"${C[@]}" exec -T postgres psql -U postgres -d osa -qtAc "SELECT pg_switch_wal()" >/dev/null
if "${C[@]}" exec -T backup with-backup-secrets pgbackrest --stanza=osa check >/dev/null 2>&1; then
  echo "ok   WAL archiving works"; else echo "FAIL WAL archiving"; leaks=1; fi
for _ in $(seq 1 30); do
  backup_gauge="$(query 'backup_last_success_timestamp_seconds{job="worker"}' || true)"
  grep -q '"value"' <<<"$backup_gauge" && break
  sleep 2
done
grep -q '"value"' <<<"$backup_gauge" && echo "ok   monitoring sees the last backup" ||
  { echo "FAIL monitoring does not see the backup: ${backup_gauge:0:200}"; leaks=1; }
# Restore the backup to a restore point, compare every table and decrypt every value (7.7.5).
if OSA_COMPOSE="${C[*]}" bash scripts/restore-drill.sh > "$WORK/restore-drill.log" 2>&1; then
  echo "ok   restore drill: $(tail -1 "$WORK/restore-drill.log")"
else
  echo "FAIL restore drill:"; tail -25 "$WORK/restore-drill.log"; leaks=1
fi

rules="$(prom rules || true)"
grep -q '"name":"ApiDown"' <<<"$rules" && grep -q '"name":"UrgentAlertUnacknowledged"' <<<"$rules" \
  && echo "ok   alert rules loaded" || { echo "FAIL alert rules not loaded"; leaks=1; }
exit $leaks
