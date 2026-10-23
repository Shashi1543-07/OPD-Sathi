#!/bin/sh
# Run a command with pgBackRest's secrets taken from secret files (ADR 007, ADR 011).
#
# pgBackRest reads options from PGBACKREST_* variables; the values come from files under
# /run/secrets so they never appear in Compose files, `docker inspect` or command lines.
#   pgbackrest_cipher_pass           repo1 encryption passphrase (required)
#   pgbackrest_repo2_cipher_pass     repo2 passphrase (defaults to repo1's)
#   pgbackrest_repo2_s3_key, pgbackrest_repo2_s3_key_secret   off-site S3-compatible storage
set -eu

secret() { [ -s "/run/secrets/$1" ] && tr -d '\r\n' < "/run/secrets/$1" || true; }

PGBACKREST_REPO1_CIPHER_PASS="$(secret pgbackrest_cipher_pass)"
if [ -z "$PGBACKREST_REPO1_CIPHER_PASS" ]; then
  echo "pgbackrest_cipher_pass is missing: backups would not be encrypted" >&2
  exit 1
fi
export PGBACKREST_REPO1_CIPHER_PASS

if [ -n "${PGBACKREST_REPO2_TYPE:-}" ]; then
  export PGBACKREST_REPO2_CIPHER_TYPE=aes-256-cbc
  PGBACKREST_REPO2_CIPHER_PASS="$(secret pgbackrest_repo2_cipher_pass)"
  export PGBACKREST_REPO2_CIPHER_PASS="${PGBACKREST_REPO2_CIPHER_PASS:-$PGBACKREST_REPO1_CIPHER_PASS}"
  export PGBACKREST_REPO2_RETENTION_FULL="${PGBACKREST_REPO2_RETENTION_FULL:-4}"
  if [ "$PGBACKREST_REPO2_TYPE" = "s3" ]; then
    PGBACKREST_REPO2_S3_KEY="$(secret pgbackrest_repo2_s3_key)"
    PGBACKREST_REPO2_S3_KEY_SECRET="$(secret pgbackrest_repo2_s3_key_secret)"
    export PGBACKREST_REPO2_S3_KEY PGBACKREST_REPO2_S3_KEY_SECRET
  fi
else
  # Compose passes these through as empty strings; pgBackRest must not see an empty repo2.
  for name in $(env | sed -n 's/^\(PGBACKREST_REPO2_[A-Z0-9_]*\)=.*/\1/p'); do unset "$name"; done
fi

exec "$@"
