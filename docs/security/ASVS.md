# OWASP ASVS 4.0.3, Level 2: self-assessment

**Phase 7, task 7.4.2.** Status on 23 October 2026, for the code on `feat/phase-7-g1`. This is
the team's own assessment, chapter by chapter, with the evidence for each area. It does not
replace the independent penetration test (task 7.4.6): give this file to the tester, who
completes the requirement-by-requirement sheet and confirms or corrects every "Met".

**Status:** ✅ Met (with evidence) · 🔧 Gap, owned by the task named · ➖ Not applicable, with
the reason.

## V1 Architecture, design and threat modelling

| Area | Status | Evidence |
|---|---|---|
| Threat model per trust boundary | ✅ | `docs/THREAT_MODEL.md` (STRIDE, 10 boundaries) |
| Security controls centralised | ✅ | One authentication gate and public-route allow-list (`core/auth.py`); one crypto module (`core/crypto.py`); one audit helper (`core/audit.py`) |
| Least privilege between components | ✅ | Separate database roles (app, migrator, worker, analytics), proven by `test_least_privilege.py`; the worker holds no patient-data key |
| Secrets management architecture | ✅ | Secrets as files, none in environment or images (ADR 007); key ring and rotation (`docs/runbooks/key-rotation.md`) |
| Dependency and build integrity | ✅ | Hash-locked dependencies, images pinned by digest, weekly update PRs; release images signed with cosign with SBOM attestations (`.github/workflows/release.yml`) |

## V2 Authentication

| Area | Status | Evidence |
|---|---|---|
| Password length and strength (12+, no composition rules, breached/common list) | ✅ | `core/passwords.py`, tests in `test_account_security.py` |
| Credential storage | ✅ | Argon2id (`core/security.py`) |
| Brute-force protection | ✅ | 5 tries, 15-minute lockout (passwords and codes alike); per-address rate limits in the API and nginx |
| Multi-factor authentication | ✅ | TOTP, required for administrators (ADR 008); recovery codes single-use and hashed; no code reuse |
| Credential recovery | ✅ | Administrator reset with a one-time temporary password and forced change; break-glass CLI is audited |
| Initial and administrator-set passwords | ✅ | Must be changed at first sign-in (`must_change_password`) |
| Service authentication | ✅ | Kiosks by a device secret (hashed, rotated every 30 days); services by database roles with passwords from files |
| Look-up secrets (status links, check-in codes, activation codes) | ✅ | 256-bit random, stored as SHA-256 digests, rate-limited |

## V3 Session management

| Area | Status | Evidence |
|---|---|---|
| Session tokens: random, unpredictable, not in URLs | ✅ | Signed JWT access tokens in memory; refresh token in an httpOnly cookie |
| Cookie attributes | ✅ | `Secure`, `HttpOnly`, `SameSite=Strict`, path-scoped to `/auth` |
| Session lifetime and renewal | ✅ | Access 10 minutes; refresh 8 hours with rotation on every use and reuse detection |
| Termination | ✅ | Logout; end one session or all others; administrator "sign out everywhere"; deactivation and password reset end every session at once (millisecond revocation) |
| Re-authentication for sensitive actions | ✅ | Password change needs the current password; MFA changes need a current code |
| Restricted sessions | ✅ | A pending password change or MFA enrolment blocks every other route (`core/auth.py`) |

## V4 Access control

| Area | Status | Evidence |
|---|---|---|
| Deny by default; explicit public routes | ✅ | `PUBLIC_ROUTES` allow-list |
| Role checks on every route | ✅ | `require_role(...)` |
| Record-level (IDOR) protection | ✅ | Row-level security per hospital; department checks; `test_access_isolation.py` fails when a new record route lacks a probe |
| Administrative interfaces | ✅ | Administrator role and MFA; every action audited |
| Directory/ file listing | ➖ | No file listings are served |

## V5 Validation, sanitisation and encoding

| Area | Status | Evidence |
|---|---|---|
| Input validation | ✅ | Strict Pydantic models (`extra="forbid"`), sizes and patterns |
| SQL injection | ✅ | Bound parameters throughout; the audit search uses the SQL builder; Bandit and Semgrep in CI |
| Output encoding / XSS | ✅ | React escaping; CSP without inline script |
| CSV/formula injection | ✅ | Audit export escapes formula cells (`test_audit_search.py`) |
| Deserialisation | ➖ | No untrusted object deserialisation |
| AI output | ✅ | Schema and semantic validation, output guard, deterministic safety rules (`docs/SAFETY.md`) |

## V6 Stored cryptography

| Area | Status | Evidence |
|---|---|---|
| Algorithms | ✅ | AES-256-GCM with associated data binding each value to its row; HMAC-SHA-256 for look-ups; Argon2id |
| Randomness | ✅ | `secrets` module for all keys, tokens, codes |
| Key management and rotation | ✅ | Key ring with versioned ciphertext; re-encryption job; escrow runbook |
| Full-disk encryption | 🔧 | Hospital IT, server specification (7.1.5) |

## V7 Error handling and logging

| Area | Status | Evidence |
|---|---|---|
| Generic error messages | ✅ | RFC 7807 problem responses without stack traces |
| Security event logging | ✅ | Append-only audit log of authentication, access and changes; searchable and exportable |
| No sensitive data in logs | ✅ | Structured logs exclude answers, names, phones and tokens by design; `tests/security/test_no_pii_in_logs.py` drives a whole visit and staff sign-in at DEBUG level and fails if any of them appears in the logs |
| Log retention and protection | ✅ | Container logs in the server's systemd journal, kept 180 days (setup-hospital.sh); logs carry no patient data |
| Time source | ✅ | NTP to NIC/NPL (`docs/DEPLOYMENT.md`) |

## V8 Data protection

| Area | Status | Evidence |
|---|---|---|
| Sensitive data in transit and at rest | ✅ | HTTPS only; field encryption |
| Client-side storage | ✅ | Kiosk session in tab storage; offline form encrypted with a non-extractable key; no-store on sensitive responses |
| Data minimisation and retention | ✅ / 🔧 | Retention command exists and is tested; approved schedule and scheduling 🔧 7.5.6 |
| Privacy controls | ✅ | Consent by purpose (care, AI, SMS), withdrawal; `docs/PRIVACY.md` |

## V9 Communications

| Area | Status | Evidence |
|---|---|---|
| TLS 1.2+ only, strong ciphers, HSTS | ✅ | `infra/web/nginx.tls.conf` (Mozilla intermediate); `scripts/check-tls.sh` |
| Internal connections | ➖ / 🔧 | Containers talk on a private Docker network on one host; TLS between them is unnecessary there. A multi-server deployment needs it (7.7.6) |
| Outbound providers | ✅ | HTTPS only (Gemini, Sarvam, Twilio, WhatsApp) |

## V10 Malicious code

| Area | Status | Evidence |
|---|---|---|
| Dependency and source scanning | ✅ | pip-audit, npm audit, Trivy, Bandit, Semgrep, Gitleaks in CI |
| Integrity of updates | ✅ | Pinned digests and hashes; signed release images, verified by `setup-hospital.sh --release-images` |

## V11 Business logic

| Area | Status | Evidence |
|---|---|---|
| Sequential flows enforced | ✅ | Intake phases enforced server-side (`FLOW_OUT_OF_ORDER`); confirmed visits immutable for patients |
| Anti-automation | ✅ | Rate limits on check-in, sign-in, status, activation; per-QR and per-phone caps |
| Safety logic cannot be lowered | ✅ | Urgency only rises without a recorded staff decision (`docs/QUEUE_POLICY.md`) |

## V12 Files and resources

| Area | Status | Evidence |
|---|---|---|
| Upload size and type | ✅ | 1 MB limit at nginx and API; content sniffing of photos |
| Storage | ✅ | Photos encrypted, never served from a public path, `no-store` |
| SSRF | ✅ | Outbound requests only to fixed provider endpoints |

## V13 API and web services

| Area | Status | Evidence |
|---|---|---|
| Schema validation, method restrictions | ✅ | FastAPI/Pydantic per route |
| CORS | ✅ | Explicit origins |
| API documentation not exposed in production | ✅ | `/docs` and `/openapi.json` absent (production smoke test) |
| Fuzzing and dynamic scanning | ✅ | Nightly, signed in as a synthetic administrator: ZAP baseline and active API scan, Schemathesis (`scripts/dast.sh`, `.github/workflows/nightly-security.yml`); warnings decided in `infra/zap/zap-rules.tsv` |

## V14 Configuration

| Area | Status | Evidence |
|---|---|---|
| Hardened build and runtime | ✅ | Non-root containers, read-only root filesystems, capabilities dropped, no new privileges (ADR 007) |
| Security headers | ✅ | CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP/COEP/CORP; nginx's own error responses (429, 413, 503) carry them too, as problem JSON |
| Debug and development features off | ✅ | Production refuses development keys (`prod_smoke.sh` checks it) |
| Dependency currency | ✅ | Dependabot weekly; CI fails on known vulnerabilities |

## Open gaps (all owned by Phase 7 tasks)

1. The first signed release cut through the release workflow (built; `docs/RELEASES.md`): 7.14.3.
2. Approved retention schedule, running on a schedule: 7.5.6.
3. Full-disk encryption on the server: 7.1.5 (hospital IT).
4. Independent penetration test confirming this assessment: 7.4.6.
