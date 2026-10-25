# OPD Saathi — Phase 7: production readiness (final phase plan)

**Prepared:** 17 October 2026 · **Baseline:** `main` at `cd5f9e5` (Phases 0–6 merged, CI green) ·
**Status:** plan only, nothing in this document is implemented yet · **Data:** synthetic only until
the go-live gate in section 10 is signed.

Phases 0–6 (`IITB_READINESS_PLAN.md`) built and tested every feature in the IIT Bombay plan. That
made OPD Saathi a working, well-tested product for a demonstration. It did not make it safe to run
on a real hospital's server with real patients. This last phase closes that gap: security, deployment,
scaling, backups, monitoring, legal and clinical sign-off, the features still missing from SIH26047, and
polish. It replaces the short "Freeze" phase of the IITB plan (final regression, real-phone LAN check,
limitations document, release tag); those items are tasks 7.13.1, 7.11.11 and 7.14.2 below.

Every finding in section 3 was checked against the code or the running stack on 17 October 2026.

---

## 1. Summary

**What is already production-grade** and must be kept: tenant isolation by PostgreSQL row-level
security with composite tenant keys; AES-256-GCM field encryption bound to each row; least-privilege
database roles (app, migrator, worker, analytics); Argon2id passwords with lockout; short-lived signed
tokens; an explicit public-route allow-list; idempotent staff writes; an outbox and a rebuildable Redis
queue; deterministic safety rules that AI can only add caution to; consent by purpose; an append-only
audit log and visit timeline; 965 API tests (96% core/domain coverage), 96 web unit tests, 144
Playwright tests, evaluation sets, and a CI that runs lint, types, tests, Bandit, Semgrep, pip-audit,
npm audit and Gitleaks on every change.

**What is missing** falls into four groups:

1. **Two defects found while writing this plan** (section 3, F1 and F2): the worker's SMS delivery step
   fails every 2 seconds on the running stack, and the SMS opt-in box ticks itself when a patient types
   a phone number. Both are fixed first (workstream 7.0).
2. **Running it safely on a hospital server**: HTTPS, a production configuration that cannot fall back
   to development secrets, key management, account recovery and MFA, scanning and a penetration test,
   backups that are encrypted, off-site and restored on a schedule, monitoring that wakes a person up,
   and capacity measured for a real OPD day (workstreams 7.1–7.4, 7.7–7.9, 7.13, 7.14).
3. **Things software cannot provide**: clinical and native-speaker sign-off of every question, rule and
   weight; DPDP Act notices, processor agreements, a retention schedule and a breach procedure; AI
   provider contracts; a regulatory assessment; hospital IT for certificates, network and storage
   (workstreams 7.5, 7.6, 7.16). These need named people outside the team and start in week 1.
4. **Product completion**: the doctor's own notes, a review-of-systems screen, a timeline of reviewed
   papers, the admin screens the API already supports, a real SMS provider, a FHIR export, and the road
   to ABHA/ABDM (workstreams 7.10–7.12).

**Size.** 17 workstreams and 131 tasks. With three engineers, about 12 weeks to a pilot-ready
v1.0 (section 5), provided the clinical and legal tracks run in parallel from week 1. ABDM certification
is a separate, longer external process and does not block a v1.0 pilot.

**Order.** Fix → deploy securely → operate (backups, alerts) → verify (pentest, load, usability) →
pilot → go-live. Nothing reaches a real patient before gate G3 in section 5.

---

## 2. What "production-ready" means here

OPD Saathi is production-ready when every line below has evidence attached (a test, a report, a signed
document) and the go-live checklist (section 10) is signed.

| Area | Done when |
|---|---|
| Security | HTTPS only; no development default can be used in production (the stack refuses to start); no open high or critical finding from an independent penetration test, image scan, DAST or API fuzzing; OWASP ASVS Level 2 self-assessment complete; admin accounts use MFA |
| Privacy and legal | Patient notice and consent text approved by counsel in all three languages; processor agreements signed with the hospital and every sub-processor (AI, speech, SMS); retention schedule approved and running on a schedule; data-request and breach procedures written and rehearsed |
| Clinical safety | Every item in `CLINICIAN_REVIEW.md` signed off by a named clinician (Ayurveda items by a vaidya) and every Hindi/Marathi text by a native speaker; hazard log reviewed; pilot results meet the thresholds in 7.6.5 |
| Reliability | Agreed recovery targets met in a restore drill held in the last 90 days; encrypted off-site backups; paper fallback rehearsed; UPS on the server |
| Performance | Peak-hour load for the hospital's size (7.8.1) with p95 under 1.5 s per step and no errors; an 8-hour soak with stable memory and connections |
| Operations | Alerts reach a named person within 5 minutes; a runbook exists for every alert; support contacts and hours agreed |
| Release | Signed `v1.0.0` tag, SBOM, release notes; upgrade from the previous release and rollback both tested |
| People | Nurses, doctors and admins trained; help at the kiosk for the first weeks |

---

## 3. Findings from the codebase analysis

Severity: **P0** must be fixed before any real patient · **P1** before go-live beyond the pilot or a
second hospital · **P2** improves safety or operability.

| # | Sev. | Finding (verified) | Evidence | Task |
|---|---|---|---|---|
| F1 | **P0** | **The worker's notification step fails every 2 seconds.** By design the worker has no data key (`APP_DATA_KEY: ""`), but `deliver_pending_once` decrypts patients' phone numbers in the worker. On the demo stack: `cron:relay_queue_outbox failed, ValueError: APP_DATA_KEY must decode to exactly 32 bytes`, 300 times in 10 minutes, with two notifications stuck as `pending`. Queue updates still work (they commit first), but no SMS can ever be sent and the error noise would hide real failures. Nothing measures worker job failures, so this went unnoticed. | `docker-compose.yml:156`, `services/api/app/notifications/queue.py:178`; worker log, `notifications` table | 7.0.1 |
| F2 | **P0** | **SMS opt-in ticks itself.** Typing a 10-digit phone number sets the opt-in, although the label says "optional; off by default". Consent must be a clear, affirmative act (DPDP). Three visits on the demo database have SMS opt-in set. | `apps/web/src/app/KioskIntake.tsx:1044` | 7.0.2 |
| F3 | **P0** | **No HTTPS.** The web container listens on port 80 only. Secure cookies (set outside development) and HSTS (set in production) assume TLS; without it the microphone and the encrypted offline form are unavailable on every device except the server itself. | `infra/web/nginx.conf:7`, `services/api/app/api/v1/auth.py:139`, `services/api/app/main.py:147`, `docs/MOBILE_LAN.md` | 7.2.1 |
| F4 | **P0** | **Production mode has never run.** Compose defaults to `APP_ENV=development` and falls back to the published development signing keys when `.env` lacks them. The production checks on keys only run when `APP_ENV=production`, and no test or CI job starts the stack that way. | `docker-compose.yml:90,108,110`, `services/api/app/config.py:143` | 7.1.1, 7.1.2 |
| F5 | P1 | **Settings that nothing reads.** `.env.example` documents `DATABASE_READ_URL`, `DB_POOL_MIN`, `DB_POOL_MAX`, `WEB_CONCURRENCY`, `MAX_QUESTIONS` (it says 12; the engine's fixed cap is 17), `S3_*`, `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT` and `READBACK_LLM`. An operator who changes them changes nothing. | `.env.example`, `services/api/app/config.py`, `services/api/app/domain/interview/engine.py:26` | 7.0.3 |
| F6 | P1 | **Fixed capacity.** Gunicorn always runs 2 workers with default timeouts and no recycling; the database pool is SQLAlchemy's default per process and cannot be configured; no PgBouncer. | `docker-compose.yml:88`, `services/api/Dockerfile`, `services/api/app/db/session.py:12` | 7.1.3, 7.8.4 |
| F7 | P1 | **Monitoring sees only HTTP, per process.** Request metrics live in each Gunicorn worker's memory; nothing counts worker jobs, outbox age, notifications, AI or speech failures, queue depth or connections; alert rules have no receiver. | `services/api/app/main.py:56`, `docs/OBSERVABILITY.md` | 7.9.1, 7.9.2 |
| F8 | P1 | **Backups are manual and partial.** `backup.sh` takes a `pg_dump` and a Redis snapshot when someone runs it: no schedule, no encryption of the archive, no off-site copy, no expiry, no point-in-time recovery. The restore check compares one table's row count and never loads Redis. | `scripts/backup.sh`, `scripts/restore.sh:43` | 7.7.2–7.7.5 |
| F9 | P1 | **Retention is a manual command.** The retention script works and is tested, but nothing schedules it and the periods are not approved. | `services/api/scripts/retention.py`, `docs/PRIVACY.md` | 7.5.6 |
| F10 | P1 | **One of everything.** One server, one PostgreSQL, one Redis; no standby. Redis `appendfsync everysec` can lose about a second of session and queue state in a crash (the queue itself is rebuilt from PostgreSQL). | `docker-compose.yml:32` | 7.7.4, 7.7.6 |
| F11 | P1 | **Supply chain.** Base images are pinned by tag, not digest; the web image is built with Node 20 while CI tests with Node 24; no image scanning, DAST or API fuzzing (the `infra/zap`, `infra/k8s` and `infra/caddy` folders are empty placeholders); an SBOM is made only on request (`make sbom`). | `apps/web/Dockerfile:2`, `.github/workflows/ci.yml:80`, `infra/` | 7.0.4, 7.4.3–7.4.5 |
| F12 | P2 | **Edge hardening.** nginx accepts 10 MB bodies although the API stops at 1 MiB; the page CSP allows images from any `https:` host and WebSockets to any host; no edge rate limiting; `FORWARDED_ALLOW_IPS="*"` trusts a forwarded address from any peer that can reach the API; limits keyed by client address treat every patient behind one NAT as one phone. | `infra/web/nginx.conf:32`, `services/api/app/config.py:69`, `infra/web/security-headers.conf:11`, `docker-compose.yml:99`, `services/api/app/api/v1/intake.py:1088` | 7.2.3–7.2.5 |
| F13 | P1 | **Keys cannot be rotated.** Every secret sits in a plain `.env`; the API accepts exactly one JWT key id; there is one data key and no re-encryption path (the `v1:` prefix allows versions, but only one key exists); the Redis password is passed on the command line. | `services/api/app/core/security.py:108`, `services/api/app/core/crypto.py:14`, `docker-compose.yml:32` | 7.3.6, 7.3.7 |
| F14 | P1 | **Account recovery and admin screens.** No password reset (the user update API has no password field), no self-service password change, no MFA; no screen for editing staff, the hospital AI switch (`/admin/ai-settings` exists) or the audit log (`/audit` exists); no way to re-issue a lost OPD card. | `services/api/app/api/v1/admin.py` (`UserPatch`), `apps/web/src/app/AdminConsole.tsx`, `docs/PRIVACY.md` | 7.3.1–7.3.3, 7.11.4, 7.11.5 |
| F15 | **P0** for real data | **AI provider terms and data location.** Gemini is called through the Developer API with an AI Studio key. Google's terms for the unpaid tier allow submitted content to be used to improve its products; patient data needs a paid or enterprise agreement with a data-processing addendum, preferably with processing in India. The same applies to Sarvam. Confirm the current terms when contracting. | `services/api/app/ai/providers/llm/gemini.py:113`, `docs/PRIVACY.md` | 7.5.9 |
| F16 | **P0** for real data | **All clinical content is a draft.** The loaders refuse rules and weights unless they are marked `draft_needs_clinician_review`; consent text and translations are unreviewed. | `services/api/app/domain/triage/score.py:26`, `services/api/app/domain/redflags/engine.py`, `docs/CLINICIAN_REVIEW.md` (all "Pending") | 7.6.1, 7.6.2 |
| F17 | P2 | **Documents promised but missing.** `docs/RAG_DESIGN.md` is referenced by the IITB plan but does not exist; `docs/LIMITATIONS.md` (IITB Phase 7) does not exist; `docs/DEPLOYMENT.md` covers only Windows with Docker Desktop. | `docs/` | 7.11.11, 7.15.1 |
| F18 | P1 | **What the tests never exercise.** Real phones and tablets, HTTPS, the production images in production mode, the real API under Playwright (e2e mocks the API), a full OPD day, several hospitals under load, a restore, an upgrade from the previous release. Three e2e tests are known to be load-sensitive (phase-01 kiosk screenshots, phase-07 5,000-row timing, phase-12 offline). | `apps/web/e2e/`, `docs/E2E_SCENARIOS.md` | 7.0.6, 7.13 |

---

## 4. Workstreams and tasks

Each task lists why it is needed, how we know it is done, who owns it, its priority and its size.

**Owner:** **E** engineering team · **C** clinicians (doctors, nurses, a vaidya for Ayurveda) ·
**L** legal / privacy (counsel, the hospital's DPO or grievance officer) · **H** hospital IT and
administration · **O** operations (whoever runs the server day to day; usually H with E's support).
**Priority:** **P0** before any real patient · **P1** before go-live beyond the pilot, or a second
hospital · **P2** after go-live. **Size:** **S** up to 2 days · **M** up to a week · **L** more than a week.

### 7.0 Fix what the analysis found (week 1)

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.0.1 | **Deliver notifications without giving the worker the patient-data key (F1).** Recommended: at check-in the API also encrypts the opted-in phone number with a separate `NOTIFY_KEY` that only the worker holds, so a compromised worker exposes opted-in phone numbers and nothing clinical. Make one bad row unable to fail the batch (the decrypt sits outside the `try` today), and create no delivery work while the provider is `console` | A test runs delivery with the worker's real environment; rows reach `sent` (console) or `failed` with a reason; the worker log is clean for an hour on the stack; a worker-job-failure alert exists (7.9.2) | E | P0 | M |
| 7.0.2 | **SMS opt-in only by the patient's own tap (F2).** Box unticked by default; SMS becomes a recorded consent purpose (version, text hash, channel) like `care` and `ai`; "Stop SMS" on the status page | Web unit + e2e: typing a phone leaves the box unticked; API test: no notification without the `sms` purpose; audit entries | E, L | P0 | M |
| 7.0.3 | **Wire or remove the unread settings (F5).** Wire `WEB_CONCURRENCY`, `DB_POOL_MIN`/`DB_POOL_MAX` and `DATABASE_READ_URL` (used by 7.1.3, 7.8.4, 7.8.5); remove `MAX_QUESTIONS`, `S3_*`, `SENTRY_DSN`, `OTEL_*` and `READBACK_LLM` until a task implements them | A CI check fails when a key in `.env.example` is read by nothing | E | P1 | S |
| 7.0.4 | **Build what CI tests (F11).** Web image built with Node 24; every base image pinned by digest; a scheduled job proposes digest updates | `docker inspect` shows the pinned digests; CI and image use the same Node | E | P1 | S |
| 7.0.5 | **Worker batches survive one bad row.** Same rule for outbox relay (already true), notifications and any new job | Unit test with a poisoned row in the middle of a batch | E | P1 | S |
| 7.0.6 | **Remove the three load-sensitive e2e tests' fixed waits (F18).** Wait for content, not time; run the 5,000-row timing test in its own worker | 10 consecutive full runs with 2 workers, no retry needed | E | P2 | S |

### 7.1 Production configuration and packaging

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.1.1 | **Production compose overlay (F4).** `docker-compose.prod.yml`: `APP_ENV=production`; every secret required (`${VAR:?}`, no fallbacks); only 443 (and 80 for redirect) published; PostgreSQL, Redis and the API not published even on loopback; `restart: always`; memory and CPU limits; log rotation; read-only root filesystems with `tmpfs` where possible; `no-new-privileges`; dropped capabilities; nginx as non-root (`nginx-unprivileged`); health checks for the worker and web (none today) | `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` passes; the stack refuses to start with any missing or default secret | E | P0 | M |
| 7.1.2 | **Production smoke in CI.** Build the images, start them with the overlay and generated test secrets, migrate, create a hospital non-interactively, activate a kiosk, complete one check-in through the API, sign in as staff, call the patient, check `/readyz`; assert `/docs` and `/openapi.json` are absent and no `development-only` value is present | CI job green on every pull request | E | P0 | M |
| 7.1.3 | **Gunicorn sized and recycled (F6).** Workers from `WEB_CONCURRENCY`, request timeout, graceful timeout, `--max-requests` with jitter; WebSockets and status streams close cleanly on restart (the board already reconnects) | Load test 7.8.2 passes with a rolling restart in the middle | E | P1 | S |
| 7.1.4 | **Linux server install.** `setup-hospital.sh` doing what `setup-hospital.ps1` does, on Ubuntu 24.04 LTS (or the state's standard OS) with Docker Engine and a systemd unit that starts the stack at boot. Docker Desktop is a desktop product and its licence requires a paid subscription for larger organisations and, per Docker's terms, government entities; Docker Engine on Linux has no such requirement | Fresh VM → running hospital in under 30 minutes by following `DEPLOYMENT.md`; tested in CI on a VM image | E | P0 | M |
| 7.1.5 | **Server specification and sizing guide** per hospital size from 7.8 results; full-disk encryption (LUKS or BitLocker) for the data volume; UPS with automatic clean shutdown | Guide in `DEPLOYMENT.md`; the pilot server meets it | E, H | P0 | S |
| 7.1.6 | **Correct time.** NTP sync (CERT-In asks for NIC or NPL time servers); JWT expiry, token numbering and the IST day close-out depend on it | Clock offset monitored (7.9.1) | H, O | P0 | S |
| 7.1.7 | **Offline installation bundle.** Signed `docker save` images with checksums, so a hospital with slow or no internet can install and upgrade | Install from the bundle on a machine with no internet | E | P1 | S |
| 7.1.8 | **Hospital profile in Settings.** Name and logo per hospital (today the logo is a build-time setting, one per installation) | Two hospitals on one server show their own logos | E | P2 | S |

### 7.2 Network, HTTPS and the edge

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.2.1 | **HTTPS in the existing nginx (F3).** Port 443 with the hospital's certificate, HTTP redirected, TLS 1.2+ (Mozilla "intermediate"), HSTS on the web app, certificate renewal procedure and an expiry alert. No new proxy (ADR 001) | SSL Labs-style check on the LAN (testssl.sh) grade A; microphone and offline form work on a second PC and a phone | E, H | P0 | M |
| 7.2.2 | **Choose the certificate route with hospital IT.** (a) A public DNS name for the server's private address with an ACME certificate by DNS validation: trusted by every phone; required if patients check in on their own phones. (b) The hospital's or state's own CA, with its root installed on managed kiosks and staff PCs: enough without phone check-in. Patients' phones must never be asked to install a root certificate | Decision recorded; certificate installed; renewal tested | H, E | P0 | S |
| 7.2.3 | **Trust forwarded addresses only from nginx (F12).** `FORWARDED_ALLOW_IPS` set to the web container's address; measure whether the hospital Wi-Fi uses NAT and tune the per-address limits for phone check-in | Test: a spoofed `X-Forwarded-For` from another container is ignored | E, H | P1 | S |
| 7.2.4 | **Edge limits.** nginx `limit_req` on sign-in, check-in, status and activation routes as a first line before the API's own limits; body limit matched to the API's 1 MiB with a small margin | ZAP and load tests show 429/413 at the edge without affecting normal use | E | P1 | S |
| 7.2.5 | **Tighter browser policy.** CSP `img-src 'self' data: blob:` and `connect-src 'self'` (plus the explicit `wss://` host if a browser needs it), `upgrade-insecure-requests`; `server_tokens off`; `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy: same-origin` | Header test in CI; all e2e still pass | E | P1 | S |
| 7.2.6 | **Network segmentation with hospital IT.** Kiosks and staff PCs on a staff network; patient Wi-Fi reaches only port 443 of the server; the server's outbound traffic limited to the AI, speech and SMS providers, OS updates and NTP; SSH only from the IT network, with keys | Written network diagram; port scan from the patient Wi-Fi shows only 443 | H | P0 | M |
| 7.2.7 | **Hospital picker on the sign-in page.** `PUBLIC_HOSPITAL_DIRECTORY` lists every hospital on the server to anyone who opens `/staff`. Keep it for single-hospital LAN installs; turn it off for shared district servers | Documented per installation type | E, H | P2 | S |
| 7.2.8 | **Internet exposure stays out of v1.** Patients use the hospital Wi-Fi. Any internet-facing deployment (check-in from home) needs its own plan: WAF, DDoS protection, bot protection on phone check-in | Stated in `DEPLOYMENT.md` and the threat model | E | P0 | S |

### 7.3 Accounts, access and keys

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.3.1 | **Password reset and change (F14).** Admin reset with a temporary password and forced change at next sign-in; self-service change; both sign the person out everywhere; audited | Integration tests; e2e for reset → forced change | E | P0 | M |
| 7.3.2 | **MFA.** Time-based one-time codes (TOTP) required for administrators, optional for doctors and nurses; recovery codes; a written break-glass procedure (`create-admin` exists) that is audited. New library → ADR | Tests for enrolment, sign-in, recovery, lockout; ADR accepted | E | P0 | M |
| 7.3.3 | **Edit staff and departments.** The API already updates name, email, department and active state; add the screen. Admin unlock of a locked account | Web unit + e2e; audit entries | E | P1 | S |
| 7.3.4 | **Sessions.** List a person's active sign-ins and end them; end all on deactivation (the board already closes within a minute) | Integration test | E | P2 | S |
| 7.3.5 | **Passwords.** Keep 12+ characters and the 5-tries/15-minutes lockout; reject passwords on a common-password list | Unit test with the list | E | P1 | S |
| 7.3.6 | **Rotate keys (F13).** JWT: sign with the current key id, accept the previous one for a day, rotation runbook. Data key: key ring with the key id in each ciphertext (`v2:`), a background job that re-encrypts old rows, then retirement of the old key. `APP_HMAC_KEY` cannot be rotated without recomputing every digest, OPD card PIN, check-in QR and status link: document exactly what breaks and rotate only by a planned migration. Key escrow: two sealed copies of `.env` keys held by named people in two places | Rotation drill on staging: no one signed out unexpectedly, every encrypted field still readable | E, H | P1 | L |
| 7.3.7 | **Secrets off the command line and out of `docker inspect`.** Docker secrets (files) instead of environment variables for keys and passwords; Redis ACL users with passwords in a config file; `.env` readable only by root (Linux) or the service account (Windows) | `docker inspect` and `ps` show no secret | E | P1 | M |
| 7.3.8 | **Least privilege, proven.** Tests that the app role cannot run DDL, bypass row-level security, or change and delete audit, visit-timeline or consent rows; no service uses the PostgreSQL superuser | Integration tests in CI | E | P1 | S |
| 7.3.9 | **Kiosk device trust.** Device secrets rotated on a schedule without re-pairing by hand; kiosk lockdown (browser kiosk mode, Windows Assigned Access or Android lock-task); privacy filter on the screen; the kiosk locked to its stand | Kiosk hardening checklist passed on the pilot kiosks | E, H | P0 | M |
| 7.3.10 | **Audit log screen (F14).** Administrators search and export the audit log (API exists) for investigations; reading the log is itself audited | Web unit + e2e | E | P1 | M |

### 7.4 Security verification

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.4.1 | **Threat model** (`docs/THREAT_MODEL.md`): STRIDE for each trust boundary (kiosk, patient phone, staff browser, API, worker, PostgreSQL, Redis, AI and speech providers, SMS provider, backups, administrators) with the existing controls and the gaps | Reviewed by the team and hospital IT; gaps become tasks | E, H | P0 | M |
| 7.4.2 | **OWASP ASVS Level 2 self-assessment** (`docs/security/ASVS.md`) | Every requirement marked met, not applicable or a task | E | P1 | M |
| 7.4.3 | **Scan images and dependencies in CI (F11).** Trivy on images and the repository, failing on high or critical with written waivers (as in ADR 006); a CycloneDX SBOM attached to every release; weekly dependency-update pull requests | CI job; first report triaged | E | P0 | S |
| 7.4.4 | **DAST.** OWASP ZAP baseline and API scan (from the OpenAPI file) against the production-mode stack, nightly | No high finding open | E | P1 | M |
| 7.4.5 | **API fuzzing.** Schemathesis, stateful, against the OpenAPI file, nightly: no 5xx, no response that skips authentication | CI job green | E | P1 | M |
| 7.4.6 | **Independent penetration test** of the pilot build and server by a CERT-In-empanelled auditor (ABDM go-live also asks for a security audit, so one audit can serve both) | Report received; every high and critical fixed and re-tested | E, H | P0 | L |
| 7.4.7 | **Review before merge.** `main` already needs the three CI checks; add one approving review and `CODEOWNERS` that require a clinician-approved reviewer for `rules.yaml`, `weights.yaml`, the question bank and consent texts | Branch protection updated | E | P1 | S |
| 7.4.8 | **Security alerts.** Spikes of failed sign-ins and lockouts, bursts of "not found" on record routes (someone probing ids), rate-limit hits, administrator actions outside OPD hours | Alert rules in 7.9.2 fire in a drill | E | P1 | S |
| 7.4.9 | **Vulnerability reporting.** `SECURITY.md` with a contact and response times | File present | E | P2 | S |

### 7.5 Privacy and legal (DPDP Act 2023)

This workstream needs qualified counsel. Statements below describe what to ask, not a legal opinion.

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.5.1 | **Roles and contracts.** The hospital is the Data Fiduciary; whoever operates OPD Saathi is its Data Processor; AI, speech and SMS providers are sub-processors. Written processing agreement between hospital and operator | Signed agreement | L, H | P0 | M |
| 7.5.2 | **Patient notice.** A full privacy notice in English, Hindi and Marathi (and as audio) with what the DPDP Act and the DPDP Rules notified in November 2025 require: the data collected item by item, each purpose, how to withdraw, how to complain to the hospital and to the Data Protection Board. The kiosk and phone link to it; the short consent notices stay | Notice approved by counsel; shown in the app; posters with a QR to it | L, E | P0 | M |
| 7.5.3 | **Who gave consent.** Record whether the patient, a parent or guardian, or a representative answered (schema change). For children and for adults who cannot consent, counsel confirms when a parent's or guardian's verifiable consent is needed and whether the healthcare exemption in the Rules covers this processing | Kiosk asks "Who is answering?" for children; consent rows carry it; counsel's note filed | L, E | P0 | M |
| 7.5.4 | **Purposes, final list.** `care` (required), `ai` (optional, exists), `sms` (optional, 7.0.2), and a decision on record linking with the OPD card: its own purpose or stated in the care notice | Decision recorded; implemented | L, E | P0 | S |
| 7.5.5 | **Patients' rights in practice.** Staff tools to export everything about a patient (access request), correct it, and erase what may be erased (balanced against medical-record retention); the grievance officer's contact in the notice; response times | Procedure document; export tool tested on a synthetic patient | L, E | P1 | M |
| 7.5.6 | **Approved retention, run on a schedule (F9).** The hospital's medical records committee sets periods per record type (minimums come from medical-record rules that apply to the hospital); the worker runs the existing retention command per hospital on a schedule, with a dry-run report and audit; backups expire on the same schedule | Schedule signed; job running on staging; report reviewed | L, H, E | P0 | M |
| 7.5.7 | **Breach and incident response.** One procedure covering DPDP (tell the Data Protection Board and affected patients; the Rules ask for a detailed report within 72 hours) and the CERT-In directions of April 2022 (report cyber incidents within 6 hours; keep ICT logs for 180 days in India). Tabletop exercise with hospital IT | Procedure signed; exercise held; log retention meets 180 days (7.9.3) | L, H, E | P0 | M |
| 7.5.8 | **Where data goes.** Map every outbound flow (Gemini, Sarvam, SMS) to a country and region; prefer processing in India; the Act allows transfers except to countries the government restricts; if ABDM is integrated, its health data management policy applies too | Data-flow map in `PRIVACY.md` | L, E | P0 | S |
| 7.5.9 | **AI provider contracts (F15).** Paid or enterprise access (for example Vertex AI in an Indian region) with a data-processing addendum, no use of prompts for training, and known retention; the same for Sarvam. Until signed, production runs with `LLM_ENABLED=false` and browser speech only, which the app fully supports | Contracts signed, or AI off in production by configuration that CI checks | L, H | P0 | M |
| 7.5.10 | **Impact assessment** (`docs/DPIA.md`): necessity, risks to patients, controls; required only for significant data fiduciaries, done anyway | Reviewed by counsel and the DPO | L, E | P1 | M |
| 7.5.11 | **Regulatory classification.** A written intended-use statement ("collects history and supports the queue; does not diagnose or treat") and an assessment of whether the triage and red-flag functions make OPD Saathi software as a medical device under the Medical Devices Rules, 2017 (CDSCO). Keep every claim, screen and document inside the intended use | Assessment filed; marketing and UI text checked against it | L, C | P0 | M |

### 7.6 Clinical safety and governance

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.6.1 | **Clinical sign-off (F16)** of every row in `CLINICIAN_REVIEW.md`: consent, core questions and modules, triggers, danger tiles, the engine's field mapping, the 15 red-flag rules (false negatives first), routing, triage weights, patient messages, the staff explainer; the Ayurveda module by a vaidya. The loaders today accept only `draft_needs_clinician_review`; add an `approved` status that requires reviewer names, date and content version | Every row signed with names, dates and versions; loaders accept approved content only with that record; evaluations re-run | C, E | P0 | L |
| 7.6.2 | **Native-speaker review** of Hindi and Marathi: 1,134 interface keys per language, the question bank, consent and AI notices, recorded prompt audio (regenerated after changes) | Review record per file; prompt-audio check green | C, E | P0 | L |
| 7.6.3 | **Hazard log** (`docs/CLINICAL_SAFETY.md`): for example a missed danger sign, a wrong department, a wrong patient linked, an AI sentence the patient never said, a stale queue, a kiosk showing someone else's answers; for each the causes, the controls already built, the residual risk and an owner (modelled on DCB0129-style practice; no Indian standard mandates one) | Reviewed by the clinical lead | C, E | P0 | M |
| 7.6.4 | **Clinical change control.** A change to rules, weights or questions needs a clinician's approval, a passing evaluation and an updated golden set; `CODEOWNERS` enforce it (7.4.7) | Process in `CONTRIBUTING.md`; first change follows it | C, E | P1 | S |
| 7.6.5 | **Validation in the pilot** (with ethics-committee approval if results are to be published). Stage A shadow mode: the kiosk history is collected, the doctor still takes their own, the two are compared. Measure danger-sign and red-flag sensitivity against nurse triage, routing accuracy, completeness, time saved, doctor edit rate and AI draft acceptance. Go-live thresholds agreed before the pilot starts | Pilot report against the agreed thresholds | C, E, H | P0 | L |
| 7.6.6 | **AI quality in production.** Weekly figures for drafts accepted / edited / rejected, output-guard rejections, AI follow-up questions dropped, paper readings marked "does not match"; monthly review; written criteria for switching AI off | Report in Reports → AI quality; review minutes | C, E | P1 | M |
| 7.6.7 | **Urgent alerts reach a person.** Agreed acknowledgement time for danger-sign alerts (for example 2 minutes); unacknowledged alerts escalate beyond the board (the board already re-alerts every 60 s) to a second device or a supervisor; kiosk signage "If you feel very unwell, tell the staff now" | Drill during the pilot meets the agreed time | C, H, E | P0 | M |

### 7.7 Reliability, backup and disaster recovery

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.7.1 | **Recovery targets agreed.** Proposal: lose at most 15 minutes of data and be back within 4 hours on one server; with a standby (7.7.6), under 1 minute and 30 minutes | Signed by the hospital | H, E | P0 | S |
| 7.7.2 | **Point-in-time recovery for PostgreSQL (F8).** Continuous WAL archiving with scheduled full backups, encryption, retention and verification (pgBackRest recommended → ADR) | Restore to an exact minute on staging | E | P0 | M |
| 7.7.3 | **Encrypted off-site copies** to a second place (another building's NAS, the state data centre, or an Indian cloud bucket), with expiry matching the retention schedule; backup keys held separately from data keys | Copy verified daily; alert when a backup is missing (7.9.2) | E, H | P0 | M |
| 7.7.4 | **Redis loss, written down and tested (F10).** The queue is rebuilt from PostgreSQL (`queue-rebuild`, also done at start-up); lost: interviews in progress (patients restart), idempotency keys, rate-limit counters. Drill: delete Redis data during a simulated OPD and recover | Runbook; drill passed | E | P1 | S |
| 7.7.5 | **Restore drills.** Nightly automated restore on synthetic data in CI (restore, migration check, row counts for every table, decrypt sample fields with the escrowed keys, app smoke) and a quarterly drill on the real server's backups | CI job; drill log signed each quarter | E, O | P0 | M |
| 7.7.6 | **Standby server.** PostgreSQL streaming replica on a second machine with a written manual failover, or at least a cold spare restored within the recovery target | Failover drill within target | H, E | P1 | M |
| 7.7.7 | **When the server is down.** A paper pack (printed intake form in three languages, a manual token roll) and a procedure for entering paper visits afterwards. The kiosk's offline short form covers network loss, not server loss | Rehearsed with the pilot department | H, C | P0 | S |
| 7.7.8 | **Failure tests.** AI down (exists), Redis down, PostgreSQL down, worker down (queue goes stale: detected by outbox age), disk full; each has a known screen and an alert | Chaos script per case (extends `scripts/chaos_*.py`), nightly | E | P1 | M |
| 7.7.9 | **Safe upgrades.** Automatic backup before migrating; migrations rehearsed on a restored copy; upgrades outside OPD hours; rollback by image and, only where safe, by downgrade | Upgrade from the previous release tested in CI (7.13.3) | E | P0 | S |

### 7.8 Scaling and performance

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.8.1 | **Capacity targets per hospital size.** For example: primary health centre (200 patients a day, 2 kiosks); district hospital (2,000 a day, 10 kiosks, 30 staff screens, 300 arrivals in the peak hour); medical college (5,000+ a day). Server size per tier | Table in `SCALING.md` agreed with the pilot hospital | E, H | P0 | S |
| 7.8.2 | **Real-flow load tests.** Extend the load smoke (40 patients today) to full check-ins at the target peak, 20 doctors calling, 50 live staff boards, 1,000 open status pages, paper photos; k6 for long runs | p95 under 1.5 s per step, no errors, tokens unique and in order | E | P0 | M |
| 7.8.3 | **Soak test.** An 8-hour OPD day at realistic rates, crossing midnight so the close-out runs under load | Memory, connections, Redis memory and file handles flat | E | P0 | M |
| 7.8.4 | **Database capacity (F6).** Pool sizes configurable and a written connection budget; PgBouncer in transaction mode before many API processes (row-level security scope is set per transaction with `set_config(..., true)`, which fits transaction pooling; prove it with a test) → ADR; index review with `EXPLAIN` on a million-visit dataset; query-count gates for queue, case-sheet and analytics routes; monthly partitions for `audit_log`, `interview_turns` and `visit_events` once retention is approved | Benchmarks recorded; gates in CI | E | P1 | L |
| 7.8.5 | **Reports off the primary when needed.** Wire `DATABASE_READ_URL` to a read-only engine with lag awareness, or keep the 5-minute materialized view on the primary if measurements show it is cheap | Decision from measurement; test for the chosen path | E | P2 | M |
| 7.8.6 | **More API containers.** Several API replicas behind nginx; live boards and status pages already fan out through Redis pub/sub; prove it with 3 replicas. The Gemini circuit breaker is per process (documented, acceptable) | Load test 7.8.2 on 3 replicas | E | P1 | M |
| 7.8.7 | **More workers.** Prove that two workers never run the same cron job twice and that the outbox relay (already `SKIP LOCKED`) shares work | Integration test with two workers | E | P1 | S |
| 7.8.8 | **Photo storage growth.** Paper photos are stored in PostgreSQL (up to 1 MB each, 10 per visit). Example: 2,000 patients a day, a quarter bringing two papers of 300 KB, is about 300 MB a day, roughly 100 GB a year before retention. Decide: keep in PostgreSQL with retention, or move to S3-compatible storage on site (MinIO → ADR) with the same AES-GCM encryption | Decision; sizing in the server guide; backup sizes planned | E, H | P1 | M |
| 7.8.9 | **Real devices.** Measure the kiosk and phone screens on a low-end Android phone (2 GB RAM) and the pilot's kiosk tablets; keep the bundle budget (entry chunk 455.5 kB today) | Device report; budgets in CI | E | P1 | S |
| 7.8.10 | **A shared district server.** Fair limits per hospital, metrics per hospital, and an operator tool to add and remove a whole hospital (export, then delete); load test with 10 hospitals | Tool tested; 10-hospital load test | E | P2 | L |

### 7.9 Monitoring and operations

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.9.1 | **Metrics that cover the system (F7).** A multi-process-safe exporter (Prometheus client in multiprocess mode, or OpenTelemetry → ADR) plus: queue length per department, oldest outbox event, worker job successes and failures, notifications sent and failed, AI latency, failures and open circuit, speech failures, live connections, database pool in use, Redis memory, disk, clock offset, kiosks offline, certificate expiry | Grafana dashboard per hospital | E | P0 | M |
| 7.9.2 | **Alerts that reach a person.** Grafana alerting or Alertmanager (→ ADR) to hospital IT by email or message: API down, 5xx over 2%, p95 over 1.5 s, worker job failing (would have caught F1), outbox older than 30 s, disk over 80%, no backup for 26 hours, certificate under 21 days, kiosk offline during OPD hours, urgent alert unacknowledged for 5 minutes | Every rule fired once in a drill and reached a phone | E, O | P0 | M |
| 7.9.3 | **Logs kept 180 days in India, without patient data.** Structured logs (exist) rotated and shipped to a local store (files or Loki → ADR); a test that runs the suites with log capture and fails if answers, phone numbers, tokens or codes appear | Retention verified; PII log test in CI | E, O | P0 | M |
| 7.9.4 | **Error tracking.** Self-hosted error tracking (GlitchTip or Sentry → ADR) or a decision not to; remove the placeholder either way | Decision recorded | E | P2 | S |
| 7.9.5 | **Runbooks** (`docs/runbooks/`): one per alert, plus start/stop, upgrade, restore, key rotation, replacing a kiosk, renewing the certificate, Redis loss, disk full, AI provider outage, signing everyone out | Each used once in a drill | E, O | P0 | M |
| 7.9.6 | **Support model.** First line at the hospital (IT or the OPD desk), second line the team; contacts, hours, response times; one channel for issues; a monthly report | Signed support agreement | H, E | P0 | S |
| 7.9.7 | **System page for administrators.** Settings → System: version, last backup, kiosks, AI and voice status, disk space | Web unit + e2e | E | P1 | M |

### 7.10 Interoperability (FHIR, ABDM, hospital systems)

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.10.1 | **FHIR R4 export of one visit**: Patient, Encounter, Condition (patient-reported), AllergyIntolerance, MedicationStatement, Observation (results from accepted paper readings with their printed ranges), QuestionnaireResponse (the interview), DocumentReference (papers), Consent; aligned with the NRCeS FHIR implementation guide used by ABDM; labelled "not ABDM-certified" until 7.10.3 | Bundles validate against FHIR R4 and the guide's profiles in unit tests | E | P1 | L |
| 7.10.2 | **ABHA at check-in.** ABDM "Scan & Share" (the patient scans the hospital's ABDM QR in their ABHA app and shares their profile at registration) matches the kiosk and phone flows; ABHA verification. Needs the hospital in the Health Facility Registry, doctors in the Healthcare Professionals Registry and sandbox credentials | Works in the ABDM sandbox | E, H | P2 | L |
| 7.10.3 | **ABDM milestones** M1 (ABHA), M2 (share records as a health information provider), M3 (receive records): sandbox, functional tests, security audit, production approval. An external process of months; not a blocker for the v1.0 pilot | Production approval | E, H, L | P2 | L |
| 7.10.4 | **The hospital's own system.** Identify it (for example NIC's e-Hospital, C-DAC's eSushrut, or a vendor HIS); agree an interface: send the case sheet as PDF or FHIR, receive the hospital registration number, map departments; HL7 v2 only if the HIS needs it | Interface agreement; end-to-end test with the HIS test system | E, H | P1 | L |
| 7.10.5 | **Token printers.** Thermal slip printers at the kiosks, or a tested browser print profile for them | Slip printed on the pilot printers | E, H | P1 | S |
| 7.10.6 | **Codes in the export.** Optional SNOMED CT codes (India distributes it through NRCeS) for complaints and reported conditions in the FHIR export; no diagnosis codes, because OPD Saathi does not diagnose | Mapping reviewed by a clinician | C, E | P2 | M |

### 7.11 Finishing the product

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.11.1 | **The doctor's own notes**: examination findings, assessment and plan in the doctor's words, follow-up date; versioned and audited like the case sheet; printed on the PDF; never generated by AI | Integration + e2e; PDF shows them | E, C | P1 | M |
| 7.11.2 | **Review of systems** (SIH Module C): a short yes/no screen per body system, wording signed off by clinicians, inside the question cap | Golden set and evaluations updated | C, E | P1 | M |
| 7.11.3 | **Prior investigations and a papers timeline** (SIH Module B): accepted readings from all visits in date order with their printed-range flags, never from unreviewed readings | Integration + e2e | E | P1 | M |
| 7.11.4 | **Re-issue a lost OPD card (F14).** Staff screen that moves the card to its next version (the old PIN stops working), audited | Integration + e2e | E | P1 | S |
| 7.11.5 | **Screens for APIs that exist (F14).** Hospital AI switch (`/admin/ai-settings`); staff editing (7.3.3); audit log (7.3.10) | Web unit + e2e | E | P1 | M |
| 7.11.6 | **SMS and WhatsApp for real.** A provider account; for SMS in India, DLT registration of the hospital as sender, its header and every message template (TRAI rules); WhatsApp Business templates approved; a per-hospital switch; delivery reports; "STOP" handling. Depends on 7.0.1 and 7.0.2 | Messages delivered on the pilot; opt-out works | E, H, L | P1 | M |
| 7.11.7 | **Someone answering for the patient.** A guardian or companion flow for children and patients who cannot answer, tied to 7.5.3; the case sheet says who answered | e2e; case sheet and PDF show it | E, C, L | P0 | M |
| 7.11.8 | **Patients seeing their own records.** Needs stronger patient sign-in than a printed card (OTP or ABHA); stays after v1.0 | Decision recorded | E, L | P2 | S |
| 7.11.9 | **Drug-interaction checks** stay out until a licensed, clinically reviewed knowledge base is procured; never an unreviewed AI judgement | Decision recorded | C, L | P2 | S |
| 7.11.10 | **More languages** after 7.6.2, through the documented path (question bank, locale files, prompt audio) | First new language reviewed and released | C, E | P2 | L |
| 7.11.11 | **Promised documents (F17).** `docs/RAG_DESIGN.md` (design only: retrieval scoped by patient and hospital before search, citations, refusal when unsupported, retrieved text treated as data, no medication advice) and `docs/LIMITATIONS.md` (what OPD Saathi does not do and why) | Both files reviewed | E | P1 | S |

### 7.12 Usability, accessibility and polish

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.12.1 | **Usability test with real people**: at least 10 patients across ages, literacy and the three languages (including elderly and first-time smartphone users), 3 nurses, 3 doctors; think-aloud; completion rate, time, errors, System Usability Scale | Report; every severe issue fixed | E, C, H | P0 | M |
| 7.12.2 | **Assistive technology**: TalkBack and NVDA on kiosk, phone and staff screens; keyboard-only staff work; 200% zoom (axe scans already pass) | Issues fixed | E | P1 | M |
| 7.12.3 | **The kiosk in the room**: height, glare, a privacy filter, noise (voice in a crowded OPD may need a handset), a stand that keeps the device | Placement checklist signed for the pilot | H, E | P0 | S |
| 7.12.4 | **Words**: plain language in all three languages, the same term for the same thing everywhere, error messages that say what to do | Copy review with 7.6.2 | E, C | P1 | M |
| 7.12.5 | **Visual pass per screen** with a checklist (empty, loading and error states; long names and translations; widths 320–1600 px), extending the existing layout e2e tests | Checklist complete per screen | E | P1 | M |
| 7.12.6 | **Staff speed**: count the clicks for the commonest actions (call next, open case sheet, accept a draft, mark absent) and remove what is not needed; document the keyboard shortcuts | Before/after numbers | E | P2 | S |

### 7.13 Testing and quality gates

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.13.1 | **Real devices over HTTPS on the hospital LAN** (the IITB plan's freeze check): kiosk tablet, Windows kiosk, low-end Android, iPhone Safari, staff PCs; the 15 scenarios' main paths | Signed device checklist | E, H | P0 | M |
| 7.13.2 | **End-to-end against the real API.** Today's Playwright tests mock the API; add a suite that drives the production-mode stack (7.1.2) through the main paths | CI job green | E | P0 | M |
| 7.13.3 | **Upgrade test.** Install the previous release with data, upgrade, verify the data and that kiosks stay paired | CI job per release | E | P0 | M |
| 7.13.4 | **Restore and failure tests nightly** (7.7.5, 7.7.8) | Nightly jobs green | E | P1 | S |
| 7.13.5 | **Load and soak before every release candidate** (7.8.2, 7.8.3) | Results in the release notes | E | P1 | S |
| 7.13.6 | **Web coverage gate** (Vitest coverage), like the API's 95% gate on core and domain | Gate in CI | E | P2 | S |
| 7.13.7 | **No patient data in logs** (7.9.3) | Test in CI | E | P0 | S |

### 7.14 Releases and delivery

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.14.1 | **Staging.** A production-like server with synthetic data, updated on every merge to `main` | Staging live; used for training and drills | E | P0 | M |
| 7.14.2 | **Versions.** Semantic versions; `v1.0.0` after the go-live gate; `CHANGELOG.md`; plain-language release notes for administrators; the version shown in Settings → System | First release cut with notes | E | P0 | S |
| 7.14.3 | **Release artifacts.** Images built once in CI, pushed to a registry by digest, signed (cosign → ADR), SBOM attached; the offline bundle (7.1.7) | A hospital installs from the signed artifacts | E | P1 | M |
| 7.14.4 | **Migration policy.** Expand, migrate, contract across releases so a rollback by one release is possible; a migration that cannot be reversed says so and requires a fresh backup; long migrations rehearsed on a million rows | Policy in `CONTRIBUTING.md`; CI checks downgrade (exists) | E | P1 | S |
| 7.14.5 | **Hotfixes.** A release branch per version for urgent fixes | First hotfix released through it | E | P2 | S |
| 7.14.6 | **One command to install, upgrade or roll back** on Windows and Linux, with a backup first and health checks after | Tested in CI on both | E | P0 | M |
| 7.14.7 | **Switches per hospital** for AI, Ayurveda, SMS and phone check-in (most exist); every new risky feature ships switched off | Settings show them; defaults off | E | P1 | S |

### 7.15 Documentation, training and support

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.15.1 | **Operator guide (F17).** `DEPLOYMENT.md` rewritten for Linux and Windows servers: install, HTTPS, backups, monitoring, upgrades, key escrow, recovery | A person outside the team installs from it | E | P0 | M |
| 7.15.2 | **Guides for staff.** One-page cards for nurses, doctors and administrators in three languages | Used in training | E, C | P0 | M |
| 7.15.3 | **Signs for patients.** "How to check in", the privacy notice with a QR, "If you feel very unwell, tell the staff now" | Printed for the pilot | E, H | P0 | S |
| 7.15.4 | **Training.** Sessions on staging, a short competency check, refreshers for new staff | Attendance and checks recorded | E, H | P0 | M |
| 7.15.5 | **Keep the full documentation current** (`docs/OPD_Saathi_Documentation.pdf`) with each release | Updated for v1.0.0 | E | P1 | S |

### 7.16 Pilot and go-live

| ID | Task | Done when | Owner | Pri. | Size |
|---|---|---|---|---|---|
| 7.16.1 | **Site survey**: network and Wi-Fi isolation, power and UPS, kiosk places, HIS, IT contact, where the server lives | Survey signed | H, E | P0 | S |
| 7.16.2 | **Pilot plan**: one department for 2–4 weeks. Stage A shadow mode (no effect on the queue); stage B live queue with a nurse watching; stage C normal use. Success measures and go/no-go thresholds agreed in advance (7.6.5) | Plan signed by the clinical lead | C, H, E | P0 | S |
| 7.16.3 | **Hypercare**: the team on site in week 1 of each stage; daily review of alerts, AI quality figures and staff feedback | Daily notes | E, H | P0 | M |
| 7.16.4 | **Go-live sign-off** with the checklist in section 10, signed by the medical superintendent or clinical lead, hospital IT, the DPO or legal, and the team | Signed checklist; `v1.0.0` tagged | C, H, L, E | P0 | S |

---

## 5. Sequence and milestones

Engineering estimate for three engineers. Clinical and legal tracks start in week 1 because they take
the longest and gate the pilot.

```
Week:                1    2    3    4    5    6    7    8    9   10   11   12
G0 fix (7.0)        ███
G1 secure build          ████████████                       7.1 7.2 7.3 7.4.1-7.4.5 7.13.2
G2 operable                        ████████████             7.7 7.8 7.9 7.14
G3 pilot-ready                                 █████████    7.11 7.12 7.13 7.4.6 pentest 7.15
G4 pilot → go-live                                       ███████████   7.16, v1.0.0
Clinical (7.6)      ██████████████████████████              sign-off before G3
Legal (7.5)         ██████████████████████████              signed before G3
ABDM (7.10.2-3)               ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ sandbox → certification (months, not a G3 blocker)
```

| Gate | Week | Passes when |
|---|---|---|
| **G0 Fixed** | 1 | F1 and F2 fixed with tests; the worker log is clean; unread settings removed or wired |
| **G1 Secure build** | 4 | Production overlay and production smoke in CI; HTTPS on staging; password reset and admin MFA; image scan, DAST and fuzzing in CI with no high finding; threat model reviewed |
| **G2 Operable** | 7 | Point-in-time backups encrypted and off-site; nightly restore test; alerts reach a phone; runbooks; load and soak targets met; staging live |
| **G3 Pilot-ready** | 10 | Penetration test closed; clinical and native-speaker sign-off complete; notices, contracts, retention and breach procedure signed; usability test done and severe issues fixed; real-device HTTPS check passed; staff trained; the go-live checklist complete except for pilot results |
| **G4 Live** | 12+ | Pilot stages A–C meet the agreed thresholds; checklist signed; `v1.0.0` tagged and released |

---

## 6. Decisions needed, and from whom

| Decision | Owner | Needed by |
|---|---|---|
| Recovery targets (data loss and time to recover) | Hospital administration, IT | G1 |
| Certificate route: public DNS name, or the hospital's CA (and therefore whether patients may check in on their own phones) | Hospital IT | G1 |
| Server operating system and who runs the server | Hospital IT | G1 |
| AI at launch: a signed provider contract (which provider and region), or AI off at launch | Hospital, counsel | G1 |
| SMS at launch, and the provider | Hospital | G2 |
| Retention periods per record type | Medical records committee, counsel | G2 |
| Queue policy items in `QUEUE_POLICY.md` (grace period, where a recalled patient goes, closing time, nurses calling) | Clinical and OPD leads | G3 |
| Ayurveda module on or off; languages at launch | Clinical lead | G3 |
| MFA for doctors and nurses (required or optional) | Hospital IT, clinical lead | G1 |
| Who holds the escrowed keys | Hospital administration | G2 |
| Support hours and response times | Hospital, team | G3 |
| Pilot department and dates | Medical superintendent | G2 |

## 7. New components that need an ADR first

ADR 001 forbids new frameworks, databases or major libraries without an ADR and the owner's approval.

| Component | For | Without it |
|---|---|---|
| pgBackRest (or WAL-G) | Point-in-time recovery, encrypted backups (7.7.2) | Nightly `pg_dump` only: up to a day of data loss |
| PgBouncer | Many API processes on one database (7.8.4) | Smaller pools; fewer API processes |
| Alertmanager (or Grafana alerting, no new component) | Alerts to people (7.9.2) | Use Grafana's built-in alerting |
| Loki (or plain rotated files) | 180-day searchable logs (7.9.3) | Rotated files, searched by hand |
| GlitchTip / Sentry, self-hosted | Error tracking (7.9.4) | Logs and alerts only |
| A TOTP library (for example `pyotp`) | MFA (7.3.2) | No MFA (not acceptable for administrators) |
| MinIO (S3-compatible) | Photo storage outside PostgreSQL (7.8.8) | Photos stay in PostgreSQL |
| Trivy, OWASP ZAP, Schemathesis, cosign | CI-only security tools (7.4.3–7.4.5, 7.14.3) | Manual scans |
| Prometheus multiprocess client or OpenTelemetry | Correct metrics with several workers (7.9.1) | One API worker per container |

## 8. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Clinical or language review takes longer than planned | Pilot slips | Start in week 1; review module by module; ship only signed modules (switch the rest off) |
| The hospital has no public DNS name, so no certificate phones trust | No phone check-in with voice | Launch with kiosks and staff PCs on the hospital's CA; phones later |
| Wi-Fi isolates clients or uses NAT | Phones cannot reach the server; rate limits misfire | Site survey (7.16.1); dedicated SSID; tune limits (7.2.3) |
| No acceptable AI contract | No AI features | The app runs fully without AI; switch it off by configuration (7.5.9) |
| Lost data key | Encrypted records unreadable for ever | Key escrow in two places (7.3.6); restore drill decrypts samples (7.7.5) |
| Single server fails during OPD | Check-in stops | Paper fallback (7.7.7); standby (7.7.6); UPS |
| Photo volume grows faster than expected | Disk and backups fill | Storage decision (7.8.8); disk alerts; retention |
| Classified as a medical device | Registration needed before use | Early assessment (7.5.11); keep within the intended use |
| Staff do not adopt it | Kiosk history unused | Usability test, training, hypercare, measured edit and acceptance rates |
| Kiosk tampering or theft | Device misuse | Lockdown, stand, device secret rotation, delete kiosk remotely (exists) |
| A small team carrying all of this | Slower progress, mistakes | Strict gates; non-engineering owners named in week 1; work in priority order |

## 9. Out of scope for v1.0

Patients viewing their own records; drug-interaction checks; handwriting recognition in Indian languages
beyond what the AI reading already does as a reviewed draft; the RAG assistant (design only, 7.11.11);
internet-facing deployment; Kubernetes (the empty `infra/k8s` placeholder stays empty: a single server
with Compose is the v1.0 target).

## 10. Go-live checklist

Signed by the medical superintendent or clinical lead, hospital IT, the DPO or legal, and the team.

- [ ] G0–G3 passed, with links to the evidence
- [ ] Production overlay in use; the stack refuses development defaults; versions and digests recorded
- [ ] HTTPS on every device; certificate renewal and expiry alert tested
- [ ] Administrators use MFA; demo accounts and the demo hospital do not exist on the server
- [ ] Penetration test closed; image scan, DAST and fuzzing clean or waived in writing
- [ ] Point-in-time backups, encrypted and off-site; restore drill in the last 90 days; keys escrowed in two places
- [ ] Alerts reach a named person; runbooks; support contacts on the wall of the server room
- [ ] Load and soak targets met for this hospital's size
- [ ] Every clinical content item and every translation signed off; hazard log reviewed
- [ ] Patient notice, consent texts, processor agreements, AI and SMS contracts, retention schedule, breach procedure signed
- [ ] Retention job scheduled and its first report reviewed
- [ ] Staff trained; patient signs up; paper fallback pack at the desk
- [ ] Pilot results meet the agreed thresholds
- [ ] `v1.0.0` tagged, signed, with SBOM and release notes

## 11. Progress checklist

Where the work stands and where the next session starts: `docs/PHASE_7_HANDOFF.md`.

| Workstream | Tasks | Status |
|---|---|---|
| 7.0 Fix what the analysis found | 7.0.1–7.0.6 | 7.0.1–7.0.5 done (G0, PR #17); 7.0.6 done (G3: ten consecutive full browser runs with 2 workers and no retry, 25 Oct 2026) |
| 7.1 Production configuration and packaging | 7.1.1–7.1.8 | 7.1.1–7.1.4, 7.1.6 done (G1); 7.1.5 needs 7.8 results; 7.1.7, 7.1.8 open |
| 7.2 Network, HTTPS and the edge | 7.2.1–7.2.8 | 7.2.1, 7.2.3–7.2.5, 7.2.7 done (G1); 7.2.2, 7.2.6 with hospital IT; 7.2.8 policy |
| 7.3 Accounts, access and keys | 7.3.1–7.3.10 | 7.3.1–7.3.10 done (G1); kiosk lockdown hardware with hospital IT |
| 7.4 Security verification | 7.4.1–7.4.9 | 7.4.1–7.4.5, 7.4.7, 7.4.9 done (G1); 7.4.6 external auditor; 7.4.8 with G2 alerts |
| 7.5 Privacy and legal | 7.5.1–7.5.11 | 7.5.3 done (G3: who gave consent, on the case sheet); 7.5.2 engineering done (G3: the full notice in three languages, a draft until counsel approves it); 7.5.5 grievance contact done (G3), access-request export open; 7.5.7 procedure drafted (G1); 7.5.1, 7.5.4, 7.5.6, 7.5.8–7.5.11 need counsel, the DPO and the hospital |
| 7.6 Clinical safety and governance | 7.6.1–7.6.7 | 7.6.1 loaders and gate done (G1), sign-off by clinicians open, including every Hindi and Marathi string added in G3; 7.6.3, 7.6.4 drafted; others open |
| 7.7 Reliability, backup and recovery | 7.7.1–7.7.9 | 7.7.2–7.7.5, 7.7.7, 7.7.9 done (G2); 7.7.8 failure drills nightly (G3: Redis, PostgreSQL, worker, full disk); 7.7.1 targets proposed, to sign; 7.7.6 standby needs hardware |
| 7.8 Scaling and performance | 7.8.1–7.8.10 | 7.8.2, 7.8.3, 7.8.7 done (G2; nightly); 7.8.4 done (pool sizes and the connection budget in G0; G3: PgBouncer decision with its test, ADR 013; index review on a million visits, migration 0034; query-count gates in CI; monthly partitions wait for the approved retention schedule); 7.8.5 done (after G3): the reports' rollup is counted per day (migration 0035), today and yesterday every 5 minutes (0.1–0.2 s at a million visits, where the full refresh took 10 s, over the worker's 5-second statement limit) and every day each night (17–26 s); the reports stay on the primary; 7.8.1 capacity per tier from staging; 7.8.6, 7.8.8–7.8.10 open |
| 7.9 Monitoring and operations | 7.9.1–7.9.7 | 7.9.1–7.9.7 done (G2); alert drill and support agreement to sign with the hospital |
| 7.10 Interoperability | 7.10.1–7.10.6 | 7.10.1 done (G3: FHIR R4 export of a visit, ADR 017); 7.10.4 needs the hospital's own system; 7.10.5 needs the pilot's printers; 7.10.2, 7.10.3 (ABDM, months) and 7.10.6 (codes, a clinician's review) later |
| 7.11 Finishing the product | 7.11.1–7.11.11 | 7.11.1–7.11.5, 7.11.7, 7.11.11 done (G3); 7.11.8, 7.11.9 decided: not in v1 (ADRs 014, 015); 7.11.6 needs an SMS provider and DLT registration; 7.11.10 needs native-speaker reviewers |
| 7.12 Usability, accessibility and polish | 7.12.1–7.12.6 | 7.12.2, 7.12.4–7.12.6 done (G3: keyboard-only staff work, the board in three languages at 320–1600 px with axe, one click less to the case sheet, `STAFF_SPEED.md`); 7.12.1 usability test with real people and 7.12.3 kiosk placement with the pilot hospital |
| 7.13 Testing and quality gates | 7.13.1–7.13.7 | 7.13.2–7.13.4, 7.13.6 done (G3: real-API browser suite in CI, upgrade test, nightly drills, web coverage gate); 7.13.7 done (G2: no patient data in logs, tested in CI); 7.13.1 real devices on the hospital LAN; 7.13.5 results into the release notes at the next release candidate |
| 7.14 Releases and delivery | 7.14.1–7.14.7 | 7.14.2–7.14.4, 7.14.6 done (G2); first release `v0.7.0` cut; 7.14.7 done (G3: SMS and phone check-in switches, SMS off by default); 7.14.1 staging needs hardware; 7.14.5 on first hotfix |
| 7.15 Documentation, training and support | 7.15.1–7.15.5 | 7.15.1–7.15.3 written (G3: operator guide with an installation check, staff cards for three roles in three languages, patient signs and the privacy poster); done when a person outside the team installs from the guide, the cards are used in training and the signs are printed for the pilot; 7.15.4 training with the hospital; 7.15.5 at `v1.0.0` |
| 7.16 Pilot and go-live | 7.16.1–7.16.4 | not started (field work with the pilot hospital) |
