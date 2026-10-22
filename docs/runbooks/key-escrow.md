# Runbook: key escrow (keeping the keys safe outside the server)

**Owner:** hospital administration (holders named below) with hospital IT ·
**When:** at installation, after every key rotation, and checked every quarter in the restore
drill (`docs/runbooks/restore.md`)

## Why it matters

Patient names, phone numbers, interview answers, case sheets and photos of papers are encrypted
with `app_data_key`. Lookups (OPD card PINs, status links, phone search) use `app_hmac_key`.
**If `app_data_key` is lost, every encrypted record is unreadable for ever**, and no backup can
help: backups hold the same ciphertext. The keys must therefore exist outside the server, in two
places, held by two named people.

## What to escrow

The whole secrets directory, `/etc/opd-saathi/secrets` (Linux) or the `.env` file (Windows
installations). It holds the database and Redis passwords, the token-signing keys and the data
keys. Escrow all of it: restoring a server needs every one.

## Making the sealed copies

1. On the server, as root, write the directory to two encrypted USB drives (or, if the hospital
   has one, a hardware-backed secrets vault):

   ```sh
   tar -C /etc/opd-saathi -czf - secrets |
     gpg --symmetric --cipher-algo AES256 --output opd-saathi-keys-$(date +%F).tar.gz.gpg
   sha256sum opd-saathi-keys-*.tar.gz.gpg
   ```

   Choose a long passphrase (at least six random words). The passphrase is split from the drive:
   it goes in a sealed envelope held by a different person from the one who holds the drive.

2. Record in the key register (paper, kept by the medical superintendent's office): the date,
   the SHA-256 printed above, who holds copy 1 and where, who holds copy 2 and where (a
   different building), and who holds each passphrase envelope.

3. Delete the local `.gpg` file from the server: `shred -u opd-saathi-keys-*.tar.gz.gpg`.

## Quarterly check

During the restore drill, one holder brings a copy: decrypt it on the drill machine, restore the
latest backup with it, and decrypt a sample of records (`scripts/restore.sh --verify-decrypt`).
Record the result in the drill log. A copy that fails is replaced the same day.

## After a key rotation

Make new sealed copies of the new directory and destroy the old ones only after the rotation
runbook (`docs/runbooks/key-rotation.md`) says the old key is retired.

## If a copy is lost or a holder leaves

Treat it as a possible key exposure: tell hospital IT and the data protection officer, and plan
a key rotation (`docs/runbooks/key-rotation.md`). Make new copies and update the register.
