# Threat model

**Phase 7, task 7.4.1.** STRIDE for each trust boundary of a single-hospital installation (one
server on the hospital LAN, `docs/DEPLOYMENT.md`). Review it with hospital IT before go-live and
after any change to a boundary; the penetration test (task 7.4.6) is scoped from it.

**STRIDE:** **S**poofing · **T**ampering · **R**epudiation · **I**nformation disclosure ·
**D**enial of service · **E**levation of privilege. **Status:** ✅ in place (with where) ·
🔧 added in Phase 7 (task) · ⚠️ residual risk, accepted or owned outside the code.

## System and boundaries

```
 Patient's phone ─┐                     ┌─ Gemini / Sarvam (AI, speech)   [B7]
 Kiosk tablet ────┤  HTTPS (hospital    │
 Staff PC ────────┤  LAN or Wi-Fi) [B1-3]│
                  ▼                     │
        ┌──────────────── server ───────┼──────────────────────────────┐
        │ web (nginx, TLS) ──▶ API ─────┴──▶ SMS / WhatsApp provider [B8]│
        │                 [B4]  │   ▲                                   │
        │                       ▼   │                                   │
        │   PostgreSQL [B6] ◀── worker [B5] ──▶ Redis [B6]              │
        └───────────────────────────────────────────────────────────────┘
              │ backups [B9]                 ▲ administrators, operators [B10]
```

## B1 · Kiosk (shared, unattended device in the OPD)

| Threat | Controls | Status |
|---|---|---|
| S: someone pairs a rogue device as a kiosk | One-time activation codes (24 h, hashed), issued by an administrator per kiosk; device secret hashed server-side | ✅ `api/v1/auth.py`, `admin.py` |
| S/T: a stolen kiosk keeps adding visits | Administrator deletes the kiosk; its device secret stops at once; kiosk tokens last 12 h; device secret rotation without re-pairing | ✅ / 🔧 7.3.9 |
| I: the next patient sees the previous patient's answers | Session in tab-scoped storage; the session ends on confirm, quit and timeout; nothing from earlier visits is ever shown on a kiosk (OPD card link shows only a date) | ✅ `KioskIntake.tsx`, `intake.py` |
| I: shoulder-surfing in a crowded OPD | Privacy filter, placement checklist | ⚠️ 7.12.3, 7.3.9 (hospital) |
| T: injected script reads the kiosk credential | CSP `script-src 'self'`, no inline script; React escaping | ✅ `infra/web/security-headers.conf` |
| E: the kiosk browser is used for anything else | Browser kiosk mode, Windows Assigned Access or Android lock-task | ⚠️ 7.3.9 (hospital IT) |
| D: kiosk floods check-ins | Per-kiosk and per-session rate limits; edge limits in nginx | ✅ / 🔧 7.2.4 |

## B2 · Patient's own phone (phone check-in and status page)

| Threat | Controls | Status |
|---|---|---|
| S: someone else's phone uses a patient's status link | Status token is 256-bit, opaque, hashed at rest; the page shows only token, position and department; it can stop AI help and SMS, never start them | ✅ `api/v1/queue.py` |
| S: a photographed QR poster is used from outside | Check-in QR is per hospital, replaceable at once; LAN only (no internet exposure in v1) | ✅ / ⚠️ 7.2.8 |
| T: forged check-ins in bulk | 6 per phone per QR in 10 min, 150 per QR in 10 min; edge limits; patients enter the queue only on confirm | ✅ / 🔧 7.2.4 |
| I: data left on a shared phone | `localStorage` holds only today's token, status link and OPD card; cleared on a later day | ✅ (documented in `PRIVACY.md`) |
| I: interception on hospital Wi-Fi | HTTPS with HSTS; patient Wi-Fi reaches only port 443 | 🔧 7.2.1 / ⚠️ 7.2.6 (hospital IT) |
| D: patients behind one NAT share a rate limit | Per-session limits; NAT measured at the site survey and limits tuned | ⚠️ 7.2.3, 7.16.1 |

## B3 · Staff browser

| Threat | Controls | Status |
|---|---|---|
| S: password guessing | Argon2id; 12+ characters; common-password list; lockout after 5 tries for 15 min; sign-in rate limit at API and edge | ✅ / 🔧 7.3.5, 7.2.4 |
| S: stolen password | TOTP second step, required for administrators | 🔧 7.3.2 (ADR 008) |
| S: stolen session | 10-minute access tokens in memory; refresh token in an httpOnly, Secure, SameSite=Strict cookie scoped to `/auth`, rotated on use with reuse detection (family revoked) | ✅ `api/v1/auth.py` |
| R: a staff member denies an action | Append-only audit log of sign-ins, views of case sheets, history, photos and exports, every change; the app role cannot update or delete it | ✅ `core/audit.py`, migrations |
| I: staff of another hospital or department see records | PostgreSQL row-level security per hospital; department checks on every record route, enforced by a test that fails when a new route lacks one | ✅ `test_access_isolation.py` |
| E: a nurse or doctor reaches admin functions | Role checks per route; explicit public-route allow-list | ✅ `core/auth.py` |
| T: CSRF | Bearer tokens for API calls; refresh cookie SameSite=Strict and path-scoped | ✅ |
| D: account lockout used to lock staff out | Lockout is time-limited; admin unlock; alert on lockout spikes | ✅ / 🔧 7.3.3, 7.4.8 |

## B4 · Web server and API

| Threat | Controls | Status |
|---|---|---|
| S: spoofed client address defeats rate limits | nginx overwrites `X-Forwarded-For`; the API trusts it only from the web container's address | ✅ / 🔧 7.2.3 |
| T: malformed or oversized input | Strict Pydantic models (`extra="forbid"`), 1 MiB body limit at API and edge, photo type sniffing | ✅ / 🔧 7.2.4 |
| I: API docs or schema reveal the attack surface | `/docs` and `/openapi.json` off in production; checked by the production smoke test | ✅ / 🔧 7.1.2 |
| I: error details leak internals | RFC 7807 problem responses without stack traces; validation errors do not echo credentials | ✅ `core/errors.py` |
| I: patient data in logs | Structured logs without answers, names, phones or tokens; log-capture test | ✅ / 🔧 7.9.3 |
| D: request floods | Edge `limit_req`/`limit_conn`; API per-route limits; Gunicorn worker recycling; resource limits per container | 🔧 7.2.4, 7.1.1, 7.1.3 |
| E: a vulnerable dependency | Hash-locked dependencies; pip-audit, npm audit, Bandit, Semgrep, Trivy; weekly update PRs; images pinned by digest | ✅ / 🔧 7.4.3, 7.0.4 |
| E: container escape or lateral movement | Non-root containers, no new privileges, capabilities dropped, read-only root filesystems; only 443/80 published | 🔧 7.1.1 |

## B5 · Worker

| Threat | Controls | Status |
|---|---|---|
| I: a compromised worker reads clinical data | The worker has no `APP_DATA_KEY`; its database role cannot read patients' stored phone numbers or names; it holds only `NOTIFY_KEY` for today's opted-in numbers | ✅ 7.0.1 (migration 0023) |
| T: one bad row stops the queue relay | Savepoint per outbox event and per notification | ✅ 7.0.5 |
| D: the worker stalls and queues go stale | Heartbeat health check restarts it; outbox-age alert | 🔧 7.1.1, 7.9.2 |

## B6 · PostgreSQL and Redis

| Threat | Controls | Status |
|---|---|---|
| I: a database copy is read | Field encryption (AES-256-GCM, bound to row and column) for names, phones, answers, case sheets, photos; full-disk encryption on the server | ✅ / ⚠️ 7.1.5 (hospital IT) |
| T: a row is moved to another record | Associated data binds each ciphertext to its row; moved ciphertext fails authentication | ✅ `core/crypto.py` |
| E: the app role escapes its tenant | `FORCE ROW LEVEL SECURITY`; composite tenant keys; least-privilege roles (app, migrator, worker, analytics); tests that the app role cannot run DDL, bypass RLS or alter audit, timeline or consent rows | ✅ / 🔧 7.3.8 |
| I: Redis contents read | Password-protected, not published, password not on the command line; holds queue, sessions, rate limits (no clinical records after confirm) | ✅ / 🔧 7.3.7 |
| D: Redis lost | Queue rebuilt from PostgreSQL at start-up; documented loss (in-progress interviews restart) | ✅ / 🔧 7.7.4 |

## B7 · AI and speech providers (Gemini, Sarvam)

| Threat | Controls | Status |
|---|---|---|
| I: patient data used to train a provider's models | No AI for a patient without their consent; hospital AI switch; production runs with AI off until a paid/enterprise contract with a data-processing addendum is signed | ✅ / ⚠️ 7.5.9 (contract) |
| I: identifiers sent to the provider | Redaction before every request; no names or phone numbers sent | ✅ `ai/providers/llm/redaction.py` |
| T: prompt injection through answers or photos | Model output validated by schema and semantic checks; output guard; deterministic safety rules decide urgency and AI can only add caution; drafts are labelled and reviewed by the doctor | ✅ `ai/output_guard.py`, evaluations |
| D: provider outage or slowness | 2.5 s budget, circuit breaker, deterministic fallback; patients never see provider errors | ✅ |

## B8 · SMS / WhatsApp provider

| Threat | Controls | Status |
|---|---|---|
| I: health information in messages | Fixed, allow-listed templates with the token and queue position only | ✅ `notifications/providers.py` |
| S: messages to someone who did not ask | Only by the patient's own tick, recorded as `sms` consent; Stop SMS; hourly cap per number | ✅ 7.0.2 |
| ⚠️ Provider contract, DLT registration of sender and templates (TRAI) | | 7.11.6 |

## B9 · Backups

| Threat | Controls | Status |
|---|---|---|
| I: a stolen backup is read | Encrypted archives (pgBackRest repository encryption); field encryption inside; backup keys held apart from data keys | 🔧 7.7.2, 7.7.3 |
| T/D: backups missing or unusable when needed | Nightly automated restore test; quarterly drill on real backups; alert when a backup is missing | 🔧 7.7.5, 7.9.2 |
| D: keys lost with the server | Key escrow in two places (`docs/runbooks/key-escrow.md`); drill decrypts samples | 🔧 7.3.6 |

## B10 · Administrators and operators

| Threat | Controls | Status |
|---|---|---|
| E: an administrator abuses access | MFA; every admin action audited; the audit log is append-only and searchable; reading it is itself audited | 🔧 7.3.2, 7.3.10 |
| R: an operator's server actions are untraceable | SSH only from the IT network with personal keys; `create-admin` and `reset-mfa` break-glass commands are audited | ⚠️ 7.2.6 / 🔧 7.3.2 |
| I: secrets visible to other server users | Secrets in a root-only directory, mounted as files; none in environment variables, `docker inspect` or `ps` | 🔧 7.3.7 |
| T: a malicious or broken release | Review before merge, CODEOWNERS, signed images with SBOM, upgrade with automatic backup and tested rollback | 🔧 7.4.7, 7.14.3, 7.7.9 |

## Accepted residual risks (v1.0)

1. **Single server.** A hardware failure stops the OPD until restore; mitigated by paper
   fallback, UPS and a standby (7.7.6, 7.7.7).
2. **Patients behind one NAT** share edge limits; generous limits plus per-session limits.
3. **Internet exposure is out of scope.** Any internet-facing deployment needs its own threat
   model (WAF, DDoS and bot protection) before use (7.2.8).
4. **Clinical content is a draft** until signed (7.6.1); this is a safety risk, not a security
   one, tracked in `docs/CLINICAL_SAFETY.md`.

## Review record

| Date | Reviewed by | Changes |
|---|---|---|
| 22 Oct 2026 | Engineering (draft) | First version |
| | Hospital IT | |
