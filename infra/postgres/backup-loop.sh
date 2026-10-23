#!/bin/sh
# The backup service (Phase 7, tasks 7.7.2 and 7.7.3; docs/runbooks/restore.md).
#
#   backup-loop              run for ever: prepare the repository, back up on schedule
#   backup-loop run full     one full backup now (also: diff, incr), then exit
#
# Schedule: every night at BACKUP_AT_UTC (default 20:00 UTC = 01:30 IST), a full backup on
# the night into Sunday IST and a differential backup the other nights. WAL is archived
# continuously by PostgreSQL itself, so any minute in between can be restored.
# After each success it writes BACKUP_STATUS_FILE, which the worker reports to monitoring;
# no status for 26 hours raises BackupMissing (docs/runbooks/alerts.md).
set -eu

STATUS_FILE="${BACKUP_STATUS_FILE:-/var/lib/osa-backup/status.json}"
AT="${BACKUP_AT_UTC:-20:00}"
STANZA=osa

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup: $*"; }

record_success() {
  tmp="$STATUS_FILE.tmp"
  printf '{"last_success": %s, "type": "%s"}\n' "$(date -u +%s)" "$1" > "$tmp"
  mv "$tmp" "$STATUS_FILE"
}

backup() {
  log "starting $1 backup"
  if pgbackrest --stanza="$STANZA" --type="$1" backup; then
    record_success "$1"
    log "$1 backup complete"
    pgbackrest --stanza="$STANZA" info
    return 0
  fi
  log "$1 backup FAILED"
  return 1
}

prepare() {
  until pg_isready -q -h /var/run/postgresql; do
    log "waiting for PostgreSQL"
    sleep 5
  done
  # Idempotent: creates the repository on the first run, checks it on every later one.
  pgbackrest --stanza="$STANZA" stanza-create
  pgbackrest --stanza="$STANZA" check
}

seconds_until() {
  now=$(date -u +%s)
  next=$(date -u -d "today $1" +%s)
  [ "$next" -le "$now" ] && next=$(date -u -d "tomorrow $1" +%s)
  echo $((next - now))
}

if [ "${1:-}" = "run" ]; then
  prepare
  backup "${2:-full}"
  exit $?
fi

prepare
if ! pgbackrest --stanza="$STANZA" --output=json info | grep -q '"label"'; then
  # A new installation (or a new repository): take the first full backup straight away.
  backup full || true
fi
while true; do
  wait=$(seconds_until "$AT")
  log "next backup in $((wait / 60)) minutes"
  sleep "$wait"
  # 20:00 UTC on Saturday is 01:30 IST on Sunday.
  if [ "$(date -u +%u)" = "6" ]; then kind=full; else kind=diff; fi
  until backup "$kind"; do
    log "retrying in one hour"
    sleep 3600
  done
done
