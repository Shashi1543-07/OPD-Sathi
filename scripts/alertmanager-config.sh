#!/usr/bin/env bash
# Write Alertmanager's configuration from infra/monitoring/alertmanager.yml.template
# (Phase 7, task 7.9.2). Used by setup-hospital.sh and the production smoke test.
#
#   ALERT_EMAIL=it@hospital.in SMTP_FROM=opd-saathi@hospital.in SMTP_HOST=smtp.hospital.in:587 \
#   SMTP_USER=opd-saathi [TELEGRAM_CHAT_ID=-1001234] scripts/alertmanager-config.sh <output file>
#
# Passwords are not written here: the SMTP password and the Telegram bot token are secret
# files (smtp_password, telegram_bot_token). Without TELEGRAM_CHAT_ID, alerts go by email only.

set -euo pipefail
OUT="${1:?Usage: scripts/alertmanager-config.sh <output file>}"
: "${ALERT_EMAIL:?ALERT_EMAIL is required (who receives alerts)}"
: "${SMTP_FROM:?SMTP_FROM is required}"
: "${SMTP_HOST:?SMTP_HOST is required (host:port)}"
SMTP_USER="${SMTP_USER:-$SMTP_FROM}"
TEMPLATE="$(dirname "$0")/../infra/monitoring/alertmanager.yml.template"

escape() { printf '%s' "$1" | sed -e 's/[\\/&|]/\\&/g'; }
config="$(sed \
  -e "s|__ALERT_EMAIL__|$(escape "$ALERT_EMAIL")|" \
  -e "s|__SMTP_FROM__|$(escape "$SMTP_FROM")|" \
  -e "s|__SMTP_HOST__|$(escape "$SMTP_HOST")|" \
  -e "s|__SMTP_USER__|$(escape "$SMTP_USER")|" \
  -e "s|__TELEGRAM_CHAT_ID__|${TELEGRAM_CHAT_ID:-0}|" \
  "$TEMPLATE")"
if [[ -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  # Drop the Telegram receiver: from its key down to its message line.
  config="$(printf '%s\n' "$config" | sed '/^    telegram_configs:/,/^        message:/d')"
fi
umask 022
printf '%s\n' "$config" > "$OUT"
