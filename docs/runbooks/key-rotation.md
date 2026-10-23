# Runbook: rotating keys

**Owner:** hospital IT with second-line support · **When:** a key may have been exposed (a lost
escrow copy, a departed administrator who had server access, an incident), or on the schedule
the hospital sets (at least yearly for the signing key) · **Time:** about an hour, outside OPD hours

Keys live in `/etc/opd-saathi/secrets` (Linux) or `.env` (Windows). Run commands from
`/opt/opd-saathi` with `C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"`.
**Take a backup first** (`scripts/backup.sh`) and make new escrow copies afterwards
(`key-escrow.md`).

## Which key, and what changing it breaks

| Key | Protects | Rotation | What breaks if it is simply replaced |
|---|---|---|---|
| `jwt_secret` | Sign-in tokens (10 min), kiosk tokens (12 h), patient sessions (2 h) | Planned, with overlap (below) | Everyone is signed out; kiosks re-authenticate with their device secret by themselves |
| `app_data_key` | Names, phones, answers, case sheets, photos, MFA secrets | Key ring + re-encryption (below) | **Every encrypted record becomes unreadable for ever.** Never replace it without the ring |
| `notify_key` | Today's opted-in SMS numbers | Replace at night | Messages pending today fail; tomorrow is unaffected (copies are erased daily) |
| `cursor_hmac_key` | Page cursors in lists | Replace any time | Open list pages restart from the top |
| `app_hmac_key` | Phone search digests, OPD card PINs, check-in QR codes, status links | **Planned migration only** | Every printed OPD card PIN stops matching, every check-in QR poster and status link stops working, phone search finds nobody. Rotate only as a planned project with reprinted cards and posters |
| Database and Redis passwords | Service connections | Change in PostgreSQL/Redis and the secret file together, then restart | Services cannot connect until both match |
| Kiosk device secrets | Each kiosk's pairing | Automatic every 30 days (task 7.3.9) | Nothing |

## The token-signing key (`jwt_secret`)

1. Generate the new key: `openssl rand -hex 32 > new_jwt_secret`.
2. Move the current key to the "previous" slot and install the new one:

   ```sh
   cd /etc/opd-saathi/secrets
   cp jwt_secret jwt_previous_secret
   install -m 0644 new_jwt_secret jwt_secret
   ```

   In `.env`: set `JWT_PREVIOUS_KID` to the current `JWT_KID` (for example `k1`) and `JWT_KID`
   to a new id (`k2`). Add `JWT_PREVIOUS_SECRET_FILE=/run/secrets/jwt_previous_secret` to the
   api and worker environment in `docker-compose.prod.yml` (and the secret entry).
3. `$C up -d api worker`. Tokens signed with either key are accepted; new ones use `k2`.
4. After 24 hours (the longest token lifetime is 12 hours), remove the previous key: delete
   `jwt_previous_secret`, clear `JWT_PREVIOUS_KID`, and `$C up -d api worker` again.

## The data key (`app_data_key`)

The data key is a ring: the current key writes, older keys still read
(`services/api/app/core/crypto.py`).

1. Generate the new key: `openssl rand -base64 32 > new_app_data_key`.
2. Put the current key into the ring under its id (the first key's id is `1`), and install the
   new one as current with a new id:

   ```sh
   cd /etc/opd-saathi/secrets
   printf '1:%s' "$(cat app_data_key)" > app_data_previous_keys
   install -m 0644 new_app_data_key app_data_key
   ```

   In `.env`: `APP_DATA_KEY_ID=2`. In `docker-compose.prod.yml`, give the api (never the
   worker) `APP_DATA_PREVIOUS_KEYS_FILE=/run/secrets/app_data_previous_keys` and the secret.
3. `$C up -d api`. New records are sealed with key 2; old ones still read.
4. Re-seal the old records (a dry run first; it only counts):

   ```sh
   $C run --rm api python -m app.cli reencrypt
   $C run --rm api python -m app.cli reencrypt --apply
   ```

   It must report `0 unreadable`. If anything is unreadable, stop: do not retire key 1, and
   contact second-line support.
5. Run the dry run again: every field shows `0 to re-seal`. Backups taken **before** this point
   still need key 1 to restore: keep key 1 in escrow until those backups expire under the
   retention schedule.
6. Retire key 1 from the running system: delete `app_data_previous_keys`, remove the variable,
   `$C up -d api`. Make new escrow copies.

## Drill

Rehearse both procedures on staging before go-live and once a year: no one may be signed out
unexpectedly during the signing-key overlap, and every encrypted field must still read after
the data key is retired (the restore drill decrypts samples).
