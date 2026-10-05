# Phase 02 — Identity, audit, and rate limiting (2026-10-05)

**Branch:** `phase-02-identity-security`
**Commit:** pending at report creation; recorded in the follow-up documentation commit.

## 1. In plain words

Staff now sign in with a hospital ID, email, and password. The API issues short-lived access tokens, keeps refresh tokens in a protected cookie, and records sensitive identity actions in an append-only audit log. PostgreSQL isolates each hospital, while Redis slows repeated login attempts. Kiosk activation codes are one-time values, and staff sessions are held in browser memory rather than local storage.

```text
Staff sign-in ──> Argon2 password check ──> short-lived access token
                         │                         │
                         ├── refresh cookie         └── role-checked API
                         └── append-only audit log
```

## 2. What was built

- **2.1–2.3:** Identity and audit migrations, forced tenant RLS, transaction-local hospital scope, Argon2id, strict JWTs, and two-hospital integration checks.
- **2.4–2.5:** Login, five-failure/15-minute lockout, refresh rotation and reuse-family revocation, logout, role dependencies, and a deny-by-default route matrix with one public-route allow-list.
- **2.6–2.8:** Redis sliding-window limits and outage behavior, kiosk provisioning and one-time activation, audit writes, and admin-only cursor pagination.
- **2.9:** Typed staff API client with in-memory access tokens and single-flight refresh, sign-in and sign-out UI, role-aware navigation, and an expired-session message.
- **2.10:** Strict CSP, production HSTS, microphone-only-self policy, CORS exposure for rate-limit headers, streamed request-body limits, and validation errors that never echo credential values.
- Added `create-admin`, `create-kiosk`, and `make demo-ratelimit` operator flows. Initial admin setup can create a synthetic hospital and writes a bootstrap audit event.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---:|---:|---|
| Route authorization matrix | 100% of registered routes | 100%; every private route returns 401 anonymously and 403 for a wrong role | Pass |
| JWT failure and claim checks | All listed cases pass | 15 security unit tests; `core/security.py` coverage 100% | Pass |
| Tenant isolation | Two hospitals; no-scope queries return zero rows | PostgreSQL integration test passed | Pass |
| Audit immutability | App role cannot update or delete | PostgreSQL permission-denied assertions passed for both operations | Pass |
| Login rate limit | Request 11 returns 429 with headers | Real Redis Lua test: request 11 denied; API test: `Retry-After: 1`, limit 10, remaining 0 | Pass |
| Redis outage behavior | Login fails closed; other limits use memory | Login returned 503; in-process limiter tests passed | Pass |
| Core security coverage | At least 95% for security and limiter modules | Security 100%; limiter 100% | Pass |
| Bandit | Zero high findings | 0 findings; scan exited successfully | Pass |
| Staff browser flows | Login, generic error, lockout, logout | 4 Playwright tests passed; login axe scan reported 0 violations | Pass |
| Dependency audit | No high npm findings; no known Python advisories | npm audit: 0 vulnerabilities; pip-audit: no known vulnerabilities | Pass |

The route matrix includes all app routes, including hidden health/readiness routes and documentation routes. It covers the currently registered `/audit` private operation; future private endpoints are checked by the same test.

## 4. Commands run

- `python -m pytest services/api/tests -q -rs` — **35 passed, 0 failed, 0 skipped**. PostgreSQL RLS and auth lifecycle tests and the real Redis limiter test ran against the local services.
- `python -m pytest tests/unit/test_security.py tests/unit/test_ratelimit.py --cov=app.core.security --cov=app.core.ratelimit --cov-report=term` from `services/api` — **20 passed**; security **100%**, limiter **100%** coverage.
- `python -m ruff check services/api/app services/api/tests` — passed.
- `python -m mypy --strict services/api/app` — passed, 29 source files checked.
- `python -m bandit -q -r services/api/app` — passed, zero findings.
- `python -m pip_audit --disable-pip -r services/api/requirements.lock.txt` — no known vulnerabilities.
- `npm test -- --reporter=dot` — **16 passed**.
- `npm run e2e -- phase-02.spec.ts` — **4 passed**: sign-in/sign-out, generic wrong password, lockout, and keyboard-only use.
- `npm run build` — passed; entry bundle **121.37 KB gzip**.
- `npm run lint`, `npm run typecheck`, and `npm run check-locales` — passed; locale parity is **170 keys per language**.
- `npm audit --audit-level=high` — **0 vulnerabilities**.
- `python -m alembic -c services/api/alembic.ini current` using the migration-role URL from `.env` — `0003_secret_lookup_rls (head)`.

## 5. Screenshots and artifacts

- [Staff sign-in screen](../screenshots/phase-02/staff-login.png)
- CI artifact upload now includes `docs/screenshots/phase-02/`.

## 6. How the human tests this phase

1. Start services, apply migrations, and launch the API and web app: `make up`, `make migrate`, `make api`, and `make web`.
2. Run `python -m app.cli create-admin`. Enter synthetic hospital details and a synthetic staff account; keep the printed hospital ID for sign-in.
3. Visit `http://localhost:5173/staff` and sign in with that hospital ID, email, and password. The staff workspace appears; sign out and confirm the form returns.
4. Use a separate synthetic account to submit five incorrect passwords. The fifth response shows the temporary lockout. Run `make demo-ratelimit` and check for HTTP 429 with `Retry-After`.
5. Run `python -m app.cli create-kiosk --hospital-id <hospital-id> --label "Demo kiosk"`. Submit its printed one-time code to `POST /kiosks/activate`; a second activation attempt fails.
6. Open `GET /audit` as an administrator and confirm bootstrap, login, lockout, logout, and kiosk events are listed. Audit events cannot be changed or deleted by the runtime app role.

## 7. Known issues and decisions

- GitHub Actions was not run from this local Windows turn. The local environment used Python 3.11.9 and Node 20.19.0; the workflow targets Python 3.12 and Node 24. The workflow includes a clean upgrade/downgrade/upgrade migration check, Gitleaks, Semgrep, and the full frontend suite; those target-version CI results remain the final external check.
- The local schema was not downgraded because that would remove existing local tables and data. The new CI job exercises reversibility against its disposable PostgreSQL service.
- No ADR was needed: PyJWT and Argon2id were already named in the locked stack. No clinical content was added.

## 8. Next phase preview

Phase 3 adds the domain data model, synthetic seeds, and pagination. It can use the hospital identity and forced RLS foundation added here.

Reply `approve phase 2` to continue.
