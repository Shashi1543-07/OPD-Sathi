#!/usr/bin/env bash
# Install or update OPD Saathi on a hospital's Linux server (Phase 7, task 7.1.4).
#
# Target: Ubuntu 24.04 LTS (or the state's standard Linux) with Docker Engine and the Compose
# plugin. Docker Engine on Linux needs no Docker Desktop licence. Run from the repository root:
#
#   sudo ./scripts/setup-hospital.sh --host opd.example-hospital.in
#
# Options:
#   --host NAME            The name staff PCs, kiosks and phones use (on the certificate). Required.
#   --add-hospital         Onboard another hospital on this server.
#   --self-signed          Staging only: create a self-signed certificate if none is installed.
#   --admin-password-file  Read the first administrator's password from this file (unattended).
#   --no-build             Use images already built or loaded (offline bundle, task 7.1.7).
#   --release-images FILE  Install the signed images of a release (its docker-compose.images.yml);
#                          their signatures are verified when cosign is installed (docs/RELEASES.md).
#
# It is safe to run again: it never replaces an existing password or key. What it does:
#   1. Creates /etc/opd-saathi/secrets (root only) with fresh random secrets, one file each.
#   2. Checks the TLS certificate in /etc/opd-saathi/certs (tls.crt with its chain, tls.key).
#   3. Checks that the clock is synchronised (NTP; CERT-In asks for NIC or NPL time servers).
#   4. Builds and starts the production stack, applies migrations, onboards the hospital.
#   5. Installs a systemd unit so the stack starts at boot.
#   Alerts (task 7.9.2) go to ALERT_EMAIL through the hospital's mail server (SMTP_FROM,
#   SMTP_HOST, SMTP_USER; password in secrets/smtp_password) and, with TELEGRAM_CHAT_ID, to a
#   Telegram group (bot token in secrets/telegram_bot_token). Asked for on the first run.
#
# Follow docs/DEPLOYMENT.md; read it before running this on a hospital server.

set -euo pipefail

HOST=""
ADD_HOSPITAL=0
SELF_SIGNED=0
PASSWORD_FILE=""
BUILD="--build"
RELEASE_IMAGES=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --add-hospital) ADD_HOSPITAL=1; shift ;;
    --self-signed) SELF_SIGNED=1; shift ;;
    --admin-password-file) PASSWORD_FILE="$2"; shift 2 ;;
    --no-build) BUILD=""; shift ;;
    --release-images) RELEASE_IMAGES="$2"; BUILD=""; shift 2 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "Run as root (sudo): the secrets directory must belong to root."
[[ -n "$HOST" ]] || fail "--host is required (the name on the hospital's certificate)."
[[ "$HOST" =~ ^[A-Za-z0-9.-]+$ ]] || fail "--host must be a plain host name or address."
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
CONFIG_DIR=/etc/opd-saathi
SECRETS="$CONFIG_DIR/secrets"
CERTS="$CONFIG_DIR/certs"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
# Images built here are labelled with this checkout's release (shown by /healthz).
APP_VERSION="$(tr -d '\r\n' < VERSION)"
export APP_VERSION
if [[ -n "$RELEASE_IMAGES" ]]; then
  # Signed release images instead of building (task 7.14.3; docs/RELEASES.md).
  [[ -f "$RELEASE_IMAGES" ]] || fail "$RELEASE_IMAGES not found (the release's docker-compose.images.yml)."
  if command -v cosign >/dev/null; then
    for image in $(sed -n 's/^ *image: *\(ghcr\.io[^ ]*\)$/\1/p' "$RELEASE_IMAGES" | sort -u); do
      cosign verify "$image" --certificate-oidc-issuer https://token.actions.githubusercontent.com \
        --certificate-identity-regexp '^https://github\.com/Shashi1543-07/OPD-Sathi/\.github/workflows/release\.yml@refs/tags/v' \
        >/dev/null || fail "The signature of $image does not verify. Do not install it."
    done
    echo "  release image signatures verified"
  else
    echo "WARNING: cosign is not installed: release image signatures were not verified."
  fi
  COMPOSE+=(-f "$(realpath "$RELEASE_IMAGES")")
fi

step "Checking Docker Engine"
command -v docker >/dev/null || fail "Docker Engine is not installed (docs/DEPLOYMENT.md, 'Prepare the server')."
docker compose version >/dev/null || fail "The Docker Compose plugin is not installed."
docker info --format '{{.ServerVersion}}' >/dev/null || fail "Docker is not running."
systemctl is-enabled docker >/dev/null 2>&1 || systemctl enable docker

step "Secrets in $SECRETS (root only)"
install -d -m 0700 -o root -g root "$CONFIG_DIR" "$SECRETS" "$CERTS"
# Each file is world-readable inside a directory only root can enter: containers running as
# their own users can read the file bind-mounted at /run/secrets, other host users cannot.
new_secret() {  # name, generator
  local file="$SECRETS/$1"
  if [[ ! -s "$file" ]]; then
    ( umask 022; "$2" > "$file.tmp" ) && mv "$file.tmp" "$file"
    echo "  created $1"
  fi
  chmod 0644 "$file"
}
hex32() { openssl rand -hex 32 | tr -d '\r\n'; }
b64key() { openssl rand -base64 32 | tr -d '\r\n'; }
empty() { :; }
for name in postgres_password osa_app_password osa_migrator_password osa_worker_password \
  redis_password jwt_secret; do
  new_secret "$name" hex32
done
for name in app_data_key app_hmac_key cursor_hmac_key notify_key; do
  new_secret "$name" b64key
done
# The backup encryption passphrase (ADR 011): separate from the data keys, escrowed with them
# (docs/runbooks/key-escrow.md). Off-site repository credentials stay empty until configured.
new_secret pgbackrest_cipher_pass hex32
for name in pgbackrest_repo2_cipher_pass pgbackrest_repo2_s3_key pgbackrest_repo2_s3_key_secret; do
  new_secret "$name" empty
done
# Optional provider keys: empty until a contract is signed (task 7.5.9).
for name in gemini_api_key sarvam_api_key twilio_auth_token whatsapp_access_token; do
  new_secret "$name" empty
done
# Alert delivery: filled in by hospital IT (the mail account's password, a Telegram bot token).
for name in smtp_password telegram_bot_token; do new_secret "$name" empty; done
read_secret() { tr -d '\r\n' < "$SECRETS/$1"; }
DB=osa
url_secret() {  # name, value
  local file="$SECRETS/$1"
  ( umask 022; printf '%s' "$2" > "$file.tmp" ) && mv "$file.tmp" "$file"
  chmod 0644 "$file"
}
url_secret database_url \
  "postgresql+asyncpg://osa_app:$(read_secret osa_app_password)@postgres:5432/$DB"
url_secret worker_database_url \
  "postgresql+asyncpg://osa_worker:$(read_secret osa_worker_password)@postgres:5432/$DB"
url_secret migrations_database_url \
  "postgresql+asyncpg://osa_migrator:$(read_secret osa_migrator_password)@postgres:5432/$DB"
url_secret redis_state_url "redis://:$(read_secret redis_password)@redis:6379/0"
url_secret redis_cache_url "redis://:$(read_secret redis_password)@redis-cache:6379/0"

step "Settings in .env (no secrets)"
if [[ ! -f .env ]]; then
  cp .env.example .env
  # The example's placeholders are for development; production reads secrets from files.
  sed -i -E '/^(POSTGRES_PASSWORD|OSA_[A-Z_]+_PASSWORD|REDIS_PASSWORD|DATABASE_URL|MIGRATIONS_DATABASE_URL|WORKER_DATABASE_URL|REDIS_STATE_URL|REDIS_CACHE_URL|JWT_SECRET|APP_DATA_KEY|APP_HMAC_KEY|CURSOR_HMAC_KEY|NOTIFY_KEY|GEMINI_API_KEY|SARVAM_API_KEY|TWILIO_AUTH_TOKEN|WHATSAPP_ACCESS_TOKEN|DEMO_[A-Z_]+|GRAFANA_ADMIN_PASSWORD)=/d' .env
fi
set_env() {
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_env WEB_ORIGIN "https://$HOST"
set_env PUBLIC_WEB_URL "https://$HOST"
set_env OSA_SECRETS_DIR "$SECRETS"
set_env OSA_CERTS_DIR "$CERTS"
# Without a provider contract AI stays off (task 7.5.9); browser speech still works.
grep -q '^LLM_ENABLED=' .env || set_env LLM_ENABLED false
chmod 0600 .env

step "TLS certificate in $CERTS"
if [[ ! -s "$CERTS/tls.crt" || ! -s "$CERTS/tls.key" ]]; then
  if [[ $SELF_SIGNED -eq 1 ]]; then
    echo "  creating a SELF-SIGNED certificate for $HOST (staging only; phones will warn)"
    openssl req -x509 -newkey rsa:3072 -sha256 -days 90 -nodes \
      -keyout "$CERTS/tls.key" -out "$CERTS/tls.crt" -subj "/CN=$HOST" \
      -addext "subjectAltName=DNS:$HOST" >/dev/null 2>&1
  else
    fail "Install the hospital's certificate as $CERTS/tls.crt (with its chain) and tls.key. See docs/DEPLOYMENT.md, 'HTTPS'."
  fi
fi
chmod 0644 "$CERTS/tls.crt"
# The web container runs as uid/gid 101 and mounts this directory: it must enter it and read
# the key; no other host user can (root:101, 0750; the key 0400 owned by 101).
chown root:101 "$CERTS" && chmod 0750 "$CERTS"
chown 101:101 "$CERTS/tls.key" && chmod 0400 "$CERTS/tls.key"
openssl x509 -in "$CERTS/tls.crt" -noout -checkhost "$HOST" | grep -q "does match" ||
  fail "The certificate in $CERTS/tls.crt is not valid for $HOST."
if ! openssl x509 -in "$CERTS/tls.crt" -noout -checkend $((21 * 86400)) >/dev/null; then
  echo "WARNING: the certificate expires within 21 days. Renew it (docs/runbooks/certificate.md)."
fi

step "Clock"
if command -v timedatectl >/dev/null; then
  if [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" != "yes" ]]; then
    echo "WARNING: the clock is not NTP-synchronised. Tokens, sign-in expiry and the end-of-day"
    echo "         close-out depend on it. Configure NIC/NPL time servers (docs/DEPLOYMENT.md, 'Time')."
  else
    echo "  synchronised"
  fi
fi

step "Logs kept 180 days"
# Containers log to the systemd journal (docker-compose.prod.yml); keep it on disk for 180
# days (CERT-In, task 7.9.3), within a size cap. Logs carry no patient data by design.
install -d -m 0755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/opd-saathi.conf <<JOURNAL
[Journal]
Storage=persistent
MaxRetentionSec=180day
SystemMaxUse=${OSA_LOG_MAX:-20G}
JOURNAL
systemctl restart systemd-journald
echo "  journal kept for 180 days (at most ${OSA_LOG_MAX:-20G})"

step "Alerts to hospital IT"
ALERTMANAGER_CONFIG=/etc/opd-saathi/alertmanager.yml
if [[ ! -s "$ALERTMANAGER_CONFIG" ]]; then
  if [[ -z "${ALERT_EMAIL:-}" && -t 0 ]]; then
    read -r -p "  Email address that receives alerts (hospital IT): " ALERT_EMAIL
    read -r -p "  Sender address for alerts: " SMTP_FROM
    read -r -p "  Mail server (host:port, for example smtp.hospital.in:587): " SMTP_HOST
    read -r -p "  Mail account user name [$SMTP_FROM]: " SMTP_USER
    read -r -p "  Telegram group chat id (empty for email only): " TELEGRAM_CHAT_ID
  fi
  [[ -n "${ALERT_EMAIL:-}" ]] ||
    fail "Set ALERT_EMAIL, SMTP_FROM and SMTP_HOST (docs/DEPLOYMENT.md, 'Alerts'); without them no one hears about a failure."
  ALERT_EMAIL="$ALERT_EMAIL" SMTP_FROM="${SMTP_FROM:-}" SMTP_HOST="${SMTP_HOST:-}" \
    SMTP_USER="${SMTP_USER:-}" TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}" \
    scripts/alertmanager-config.sh "$ALERTMANAGER_CONFIG"
  echo "  alerts go to $ALERT_EMAIL; put the mail password in $SECRETS/smtp_password"
else
  echo "  keeping $ALERTMANAGER_CONFIG"
fi
set_env OSA_ALERTMANAGER_CONFIG "$ALERTMANAGER_CONFIG"

step "Starting the database and caches"
"${COMPOSE[@]}" up -d $BUILD postgres redis redis-cache worker-role-bootstrap

step "Backing up before migrating"
if "${COMPOSE[@]}" exec -T postgres pg_isready -q 2>/dev/null &&
   [[ -x scripts/backup.sh ]] && [[ "$("${COMPOSE[@]}" exec -T postgres sh -c 'psql -U postgres -d osa -tAc "select count(*) from alembic_version"' 2>/dev/null || echo 0)" != "0" ]]; then
  # A full pgBackRest backup (restorable to the minute) and a logical dump as a second copy.
  "${COMPOSE[@]}" run --rm $BUILD backup backup-loop run full ||
    fail "Backup failed; not migrating (docs/runbooks/restore.md)."
  COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml" BACKUP_DIR=/var/backups/opd-saathi \
    ./scripts/backup.sh || fail "Backup failed; not migrating."
else
  echo "  new installation: nothing to back up"
fi

step "Applying database migrations"
"${COMPOSE[@]}" run --rm $BUILD api alembic -c /app/alembic.ini upgrade head

step "Starting the API, worker and web server"
"${COMPOSE[@]}" up -d $BUILD api worker web prometheus alertmanager backup

if [[ ! -f "$CONFIG_DIR/.onboarded" || $ADD_HOSPITAL -eq 1 ]]; then
  step "Onboarding the hospital"
  if [[ -n "$PASSWORD_FILE" ]]; then
    : "${HOSPITAL_NAME:?Set HOSPITAL_NAME, HOSPITAL_CODE, ADMIN_NAME and ADMIN_EMAIL for unattended setup}"
    "${COMPOSE[@]}" run --rm -T api python -m app.cli setup-hospital \
      --name "$HOSPITAL_NAME" --code "$HOSPITAL_CODE" \
      --admin-name "$ADMIN_NAME" --admin-email "$ADMIN_EMAIL" --admin-password-stdin \
      < "$PASSWORD_FILE"
  else
    "${COMPOSE[@]}" run --rm api python -m app.cli setup-hospital
  fi
  touch "$CONFIG_DIR/.onboarded"
fi

step "Starting at boot (systemd)"
cat > /etc/systemd/system/opd-saathi.service <<UNIT
[Unit]
Description=OPD Saathi (Docker Compose stack)
Requires=docker.service
After=docker.service network-online.target time-sync.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$REPO
ExecStart=/usr/bin/${COMPOSE[*]} up -d --remove-orphans
ExecStop=/usr/bin/${COMPOSE[*]} stop
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable opd-saathi.service >/dev/null

step "Checking the installation"
for _ in $(seq 1 30); do
  if curl -fsS --resolve "$HOST:443:127.0.0.1" "https://$HOST/api/readyz" -k >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS --resolve "$HOST:443:127.0.0.1" "https://$HOST/api/readyz" -k ||
  fail "The API is not ready. Check: docker compose -f docker-compose.yml -f docker-compose.prod.yml logs api"
echo

cat <<DONE

OPD Saathi is running for this hospital.

  Staff (PCs on the hospital network):  https://$HOST/staff
  Kiosks:                              https://$HOST/kiosk

Next: sign in as the administrator, turn on two-step sign-in (Settings -> My account), then add
staff and kiosks. Before any real patient: complete docs/GO_LIVE_CHECKLIST.md.

Keep two sealed copies of $SECRETS in two places (docs/runbooks/key-escrow.md): without
app_data_key the encrypted patient records can never be read again.
DONE
