# Runbook: renew or replace the HTTPS certificate

**Alert:** `CertificateExpiresSoon` (fewer than 21 days left) · **Owner:** hospital IT ·
**Time:** 15 minutes · **Impact while doing it:** none (nginx reloads without dropping connections)

## Why it matters

Every kiosk, staff PC and phone reaches OPD Saathi over HTTPS. When the certificate expires,
browsers refuse the site: kiosks stop, staff cannot sign in, and patients' status pages fail.
Voice input and the offline kiosk form need HTTPS too.

## Which route this hospital uses

Recorded in the hospital's site survey (`docs/DEPLOYMENT.md`, "HTTPS"):

- **(a) Public DNS name with an ACME certificate** (for example Let's Encrypt by DNS validation).
  Renew with the same ACME client and DNS credentials that issued it. Usually automatic; this
  alert means automation failed.
- **(b) The hospital's or state's own certificate authority.** Request a new certificate from
  that authority for the same host name.

## Steps

1. Get the new certificate (`tls.crt`, with the intermediate chain appended after it) and its
   private key (`tls.key`). Check them on the server:

   ```sh
   HOST=opd.example-hospital.in
   openssl x509 -in tls.crt -noout -subject -enddate -checkhost "$HOST"
   # The key matches the certificate when these two hashes are equal:
   openssl x509 -in tls.crt -noout -pubkey | sha256sum
   openssl pkey -in tls.key -pubout | sha256sum
   ```

2. Install them, keeping the old pair for rollback:

   ```sh
   sudo cp -a /etc/opd-saathi/certs /etc/opd-saathi/certs.$(date +%F)
   sudo install -m 0644 tls.crt /etc/opd-saathi/certs/tls.crt
   sudo install -m 0400 -o 101 -g 101 tls.key /etc/opd-saathi/certs/tls.key
   ```

3. Reload nginx without a restart:

   ```sh
   cd /opt/opd-saathi
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web nginx -t
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web nginx -s reload
   ```

4. Verify from a staff PC: open `https://<host>/staff` and check the padlock's expiry date.
   From the server: `scripts/check-tls.sh <host>` (or `testssl.sh <host>`) reports grade A.

## Rollback

Copy the saved `certs.<date>` directory back and reload nginx (step 3).

## Never

Never install a root certificate on a patient's phone. Never disable HTTPS to get kiosks
working; use the paper fallback (`docs/runbooks/server-down.md`) while the certificate is fixed.
