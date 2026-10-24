# Installing and running OPD Saathi at a hospital

For the engineer or hospital IT person installing OPD Saathi on a hospital's own server, and for
whoever runs it afterwards. Follow it in order. The demo (`start-demo.ps1`) is separate and
must never be used for a real hospital.

**Before any real patient:** the go-live checklist (`docs/GO_LIVE_CHECKLIST.md`) must be complete
and signed. Until then, use synthetic data only.

## 1. How it fits together

One server inside the hospital runs everything in Docker: PostgreSQL (the records), Redis
(queues and sessions), the API, the worker (queue updates, messages, end-of-day close-out) and
the web server (nginx, HTTPS). Kiosks, staff PCs and patients' phones reach it over the
hospital network at one address, `https://<host>`. Nothing is published to the internet
(`docs/THREAT_MODEL.md`, "Accepted residual risks").

Each hospital's data is kept apart by PostgreSQL row-level security, so one server can serve
several hospitals (a district hosting its PHCs); each still gets its own administrator,
departments, staff and kiosks.

## 2. Choose and size the server

| Hospital | Patients a day | Server | Disk (data) |
|---|---|---|---|
| Primary health centre | up to 200 | 4 cores, 8 GB RAM | 200 GB SSD |
| District hospital | up to 2,000 | 8 cores, 16 GB RAM | 1 TB SSD |
| Medical college | 5,000+ | 16 cores, 32 GB RAM | 2 TB SSD, plus a standby server |

The figures are planning estimates until the load tests (task 7.8) confirm them for the pilot.
Photos of old papers dominate disk use: at a district hospital, about 100 GB a year before
retention (`docs/SCALING.md`).

Also needed: a UPS with at least 15 minutes and automatic clean shutdown
(`docs/runbooks/power.md`); full-disk encryption for the data volume (LUKS on Linux, BitLocker
on Windows); a fixed address on the hospital network.

**Operating system.** Ubuntu 24.04 LTS (or the state's standard Linux) with Docker Engine is
the recommended server. Docker Engine on Linux is free to use. Docker Desktop (Windows, Mac) is a
desktop product whose licence requires a paid subscription for larger organisations and, per
Docker's terms, government entities: use it only for the demo and development.

## 3. Prepare the server (Linux)

```sh
sudo apt update && sudo apt full-upgrade -y
# Docker Engine and the Compose plugin, from Docker's own repository:
# https://docs.docker.com/engine/install/ubuntu/
sudo apt install -y git openssl curl
sudo git clone https://github.com/Shashi1543-07/OPD-Sathi.git /opt/opd-saathi
```

**Time.** Sign-in expiry, token numbering and the end-of-day close-out depend on the clock.
CERT-In asks for NIC or NPL time servers. With `systemd-timesyncd`:

```sh
sudo sed -i 's/^#\?NTP=.*/NTP=samay1.nic.in samay2.nic.in/' /etc/systemd/timesyncd.conf
sudo systemctl restart systemd-timesyncd && timedatectl show -p NTPSynchronized
```

(Confirm the current NIC/NPL server names with hospital IT; any reachable NTP source is better
than none.) Monitoring alerts on clock drift (task 7.9).

**Network** (hospital IT, task 7.2.6). Kiosks and staff PCs on the staff network. If patients
check in on their own phones, the patient Wi-Fi may reach only port 443 of this server and
must not isolate clients from it. The server's outbound traffic only to the AI, speech and SMS
providers (if contracted), OS updates and NTP. SSH only from the IT network, with keys.

## 4. HTTPS

Browsers allow the microphone and the encrypted offline kiosk form only over HTTPS. Choose a
route with hospital IT (task 7.2.2) and record it:

- **(a) A public DNS name for the server's private address** (for example
  `opd.district-hospital.in` resolving to `10.20.0.5`), with a certificate from an ACME CA by DNS
  validation. Every phone trusts it. **Required if patients check in on their own phones.**
- **(b) The hospital's or state's own certificate authority**, with its root installed on the
  managed kiosks and staff PCs. Enough when patients use kiosks only. **Never ask patients to
  install a root certificate on their phones.**

Place the certificate (with its chain appended) and key as `/etc/opd-saathi/certs/tls.crt` and
`tls.key`. Renewal: `docs/runbooks/certificate.md`. Check: `scripts/check-tls.sh <host>`.

## 5. Install

```sh
cd /opt/opd-saathi
sudo ./scripts/setup-hospital.sh --host opd.district-hospital.in
```

The script:

1. Creates `/etc/opd-saathi/secrets` (root only) with fresh random passwords and keys, one file
   each. They are mounted into the containers as files; none appears in an environment
   variable, `docker inspect` or the process list.
2. Writes the non-secret settings to `.env`. AI is off (`LLM_ENABLED=false`) until a provider
   contract is signed (task 7.5.9); voice still works with the browser's own speech.
3. Checks the certificate and the clock.
4. Builds and starts the production stack (`docker-compose.yml` with
   `docker-compose.prod.yml`): hardened containers, only ports 443 and 80 (redirect) open.
5. Applies the database migrations and onboards the hospital: its name, a short code, and the
   first administrator.
6. Installs `opd-saathi.service` so the stack starts at boot.

For an unattended install: `HOSPITAL_NAME=... HOSPITAL_CODE=... ADMIN_NAME=... ADMIN_EMAIL=...
sudo -E ./scripts/setup-hospital.sh --host ... --admin-password-file /root/admin.pw`.

**Windows Server** (only where the state mandates it): OPD Saathi's images are Linux images, and
Docker on Windows Server runs Windows containers. Create an Ubuntu 24.04 virtual machine in
Hyper-V (at least the cores, memory and disk of the table in section 2, a fixed address on the
hospital network, the virtual disk on encrypted storage) and follow this guide inside it,
unchanged. Back up and monitor the virtual machine like any other server; the stack's own
backups (section 7) still run inside it. There is no separate Windows installer: one tested
script is safer than two.

**Then, immediately:** make the sealed key-escrow copies (`docs/runbooks/key-escrow.md`).
Without `app_data_key`, encrypted records can never be read again.

### Check the installation

Do this once after installing, and after every upgrade. Each step must pass before anyone
else uses the system.

| Check | How | Passes when |
|---|---|---|
| The stack is up | `$C ps` (section 7) | Every service `running`, and `healthy` where it has a health check |
| HTTPS and readiness | `scripts/check-tls.sh <host>`; open `https://<host>/api/readyz` | The certificate is valid for the host and not expiring soon; readiness answers `{"status":"ready"}` |
| Sign-in | `https://<host>/staff`, as the first administrator | Two-step sign-in is asked for and works |
| The system page | Settings → System | Version as released; database, Redis and worker up; a last backup listed; disk space shown |
| A check-in | Register a kiosk named "Installation test" (Settings → Kiosks), open its setup link on a PC, check in a made-up patient (no real details), call them from the queue, then delete the kiosk | Token issued; the patient appears in the queue and on the doctor's screen |
| Backups | `$C exec backup with-backup-secrets pgbackrest --stanza=osa info` | A full backup listed in every configured repository |
| Alerts | The test alert in `docs/runbooks/alerts.md`, "Drill" | The named IT person receives it within 5 minutes |

Then cancel the test patient's visit at the desk, so it does not stay in the day's queue.

## 6. The administrator's first day

1. Open `https://<host>/staff` and sign in. Set up two-step sign-in when asked (an authenticator
   app on your phone); print or write down the ten recovery codes.
2. **Settings → Users:** add each doctor and nurse with their department. They choose their own
   password at first sign-in.
3. **Settings → Departments:** the standard ones exist (codes `GENMED`, `ORTHO`, `ENT`, `OBG`,
   `PAED`, `DERM`, `OPHTH`, `SURG`, which the automatic routing understands); rename or deactivate
   the ones the hospital does not have.
4. **Settings → Kiosks:** register each kiosk by location, open its setup link once on that
   kiosk. Its device secret then renews and rotates by itself.
5. For phone check-in: create a phone check-in QR and print it for the waiting area.
6. Lock the kiosks down (kiosk browser mode, Windows Assigned Access or Android lock-task) with a
   privacy filter, on a stand (task 7.3.9).
7. **Settings → Departments**, the switches below the department form. Every new or risky
   feature starts switched off; turn on only what the hospital has approved:

   | Switch | Starts | Turn on when |
   |---|---|---|
   | Patient interview: review of systems, Ayurveda history | Off | The clinical lead (a vaidya for Ayurveda) has signed the questions off |
   | Online AI help | On where the server has AI (`LLM_ENABLED=true`), which the installer leaves off | The AI provider contract is signed (task 7.5.9) |
   | Features for patients: SMS queue messages | Off | An SMS provider and registered message templates are in place (task 7.11.6); the card says when the server has no provider |
   | Features for patients: phone check-in | On | Turn it off to stop every phone check-in QR at once; kiosks keep working |

8. Print the staff cards and the patient signs (`docs/print/`) and put the signs up before the
   first patient.

## 7. Running it

From `/opt/opd-saathi`, with `C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"`:

| Task | How |
|---|---|
| Status | `docker compose -f docker-compose.yml -f docker-compose.prod.yml ps` |
| Logs (no patient data by design; kept 180 days in the journal) | `$C logs --tail 200 api worker web`, or older: `journalctl SYSLOG_IDENTIFIER=opd-saathi/<container> --since "2026-11-01"` |
| Restart | `sudo systemctl restart opd-saathi` |
| Back up now | `$C exec backup backup-loop run full` (and a logical dump: `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml BACKUP_DIR=/var/backups/opd-saathi scripts/backup.sh`) |
| Restore | `docs/runbooks/restore.md` |
| Account help | Settings → Users: reset password, unlock, reset two-step sign-in, sign out everywhere |
| Investigation | Settings → Audit log (`docs/runbooks/audit-investigation.md`) |
| Key rotation | `docs/runbooks/key-rotation.md` |
| Server down in OPD hours | `docs/runbooks/server-down.md` (paper fallback) |

Runbooks for each failure are in `docs/runbooks/`.

**Backups** (ADR 011; `docs/runbooks/restore.md`) run by themselves: every database change is
archived within 5 minutes and a backup is taken every night at 01:30 IST, encrypted. Check them
with `$C exec backup with-backup-secrets pgbackrest --stanza=osa info`, and run
`sudo scripts/restore-drill.sh` every quarter. **Keep a copy off the server** (a fire or theft
takes the server and its disks): set a second repository in `.env` and restart the stack, either
an S3-compatible bucket in India

```sh
PGBACKREST_REPO2_TYPE=s3
PGBACKREST_REPO2_PATH=/opd-saathi
PGBACKREST_REPO2_S3_BUCKET=hospital-backups
PGBACKREST_REPO2_S3_ENDPOINT=s3.example-indian-cloud.in
PGBACKREST_REPO2_S3_REGION=ap-south-1
# keys in secrets/pgbackrest_repo2_s3_key and pgbackrest_repo2_s3_key_secret
```

or a NAS in another building mounted on the server (`PGBACKREST_REPO2_TYPE=posix`,
`PGBACKREST_REPO2_PATH=/mnt/offsite`, `PGBACKREST_REPO2_HOST_PATH=/mnt/nas/opd-saathi`). Then
`$C exec backup backup-loop run full` and confirm `info` lists both repositories.

## 8. Monitoring and alerts

Prometheus and Alertmanager run with the stack (ADR 010) and tell hospital IT when something
fails: the API, web server, worker, database or Redis down; errors or slowness; a stuck queue;
an urgent alert no nurse has acknowledged for 5 minutes; a kiosk silent during OPD hours; the
disk over 80%; no backup for 26 hours; the certificate within 21 days of expiry.

The setup script asks where alerts go: an email address and the hospital's mail server (put the
mail account's password in `/etc/opd-saathi/secrets/smtp_password`), and optionally a Telegram
group (bot token in `secrets/telegram_bot_token`). For an unattended install set `ALERT_EMAIL`,
`SMTP_FROM`, `SMTP_HOST`, `SMTP_USER` and `TELEGRAM_CHAT_ID`. To change them later, edit
`/etc/opd-saathi/alertmanager.yml` and `$C restart alertmanager`.

What each alert means and the first thing to do: `docs/runbooks/alerts.md`, which also has the
drill every alert must pass before go-live. Nothing is published: to look at Prometheus, open
an SSH tunnel (`ssh -L 9090:127.0.0.1:9090 <server>`) and browse to `http://localhost:9090`.

## 9. Upgrades and rollback

1. Read the release notes; upgrade outside OPD hours.
2. `cd /opt/opd-saathi && sudo git fetch --tags && sudo git checkout v<new>`
3. `sudo ./scripts/setup-hospital.sh --host <host>`: it backs up before migrating, applies
   migrations, rebuilds and restarts, and checks readiness.
4. Check `https://<host>/api/readyz` and sign in.

**Rollback:** check out the previous tag and run the setup script again. Migrations follow
expand, migrate, contract (`CONTRIBUTING.md`), so the previous release runs on the upgraded
database; a release whose migration cannot be reversed says so in its notes and needs the
backup taken in step 3.

## 10. Support

First line: the hospital's IT person or OPD desk lead. Second line: the OPD Saathi team, by the
channel and hours agreed in the support agreement (`docs/SUPPORT.md`). Report security issues
privately (`SECURITY.md`) and incidents by `docs/INCIDENT_RESPONSE.md`.
