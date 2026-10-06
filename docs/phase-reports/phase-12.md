# Phase 12 — Offline PWA and short-form intake (7 October 2026, `phase-12-offline-intake`)

## 1. In plain words

The kiosk can collect a limited check-in during a network outage, tell patients with danger signs to see a nurse immediately, and save the submission encrypted on the device. When connectivity returns, it sends the record through the existing clinical interview and queue flow. The local receipt is clearly provisional until a server token arrives.

## 2. What was built

- Added a ten-second `/healthz` heartbeat and browser connectivity handling to the kiosk shell, with translated offline and pending-sync states.
- Added an offline short form for age, sex, chief complaint, duration, severity, conditions, medicines, allergies, and danger signs. Danger selections immediately display the nurse instruction.
- Added an IndexedDB outbox. Each record is encrypted with AES-GCM using a non-extractable key stored in IndexedDB, retried with capped backoff, and removed after server confirmation. Plaintext submissions are not stored in the service-worker cache.
- Added kiosk-local provisional receipts and a kiosk-authenticated `POST /intake/offline-submissions` endpoint. The server checks consent version/hash, runs answers through the current interview and red-flag engines, and then uses the normal case-sheet and queue flow.
- Added a PostgreSQL and Redis integration test that activates a kiosk, submits a visit, creates a fresh API app lifespan, replays the same request, and verifies the identical response and exactly one visit.
- Updated the Phase 10 offline audio gate to ignore expected offline heartbeat failures while still rejecting failed audio requests.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---:|---:|---|
| Offline form, provisional token, danger instruction, reconnect sync | Complete flow | Playwright flow passed; danger alert visible; IndexedDB contained ciphertext rather than the complaint plaintext; one replay returned the real token | Pass |
| Duplicate and restart replay | One visit, same real token | PostgreSQL + Redis integration: replay through a second API lifespan returned an identical response; database contained one visit | Pass |
| Offline accessibility | 0 serious or critical axe findings | 0 serious or critical findings in the Phase 12 browser flow | Pass |
| PWA shell and cached asset offline | Offline route and asset served by service worker | Production PWA test opened `/kiosk/offline-intake` from the cached shell and played cached prompt audio with browser network disabled; failed audio requests: 0 | Pass |
| API unit and integration tests | 0 failures | 10 passed, including the real PostgreSQL/Redis replay test | Pass |
| Frontend unit tests | 0 failures | 36 passed across 12 files | Pass |
| Frontend lint, typecheck, and locale parity | Clean | All passed; 403 keys per language | Pass |
| Production build | Succeeds | Passed; 1,708 modules; 574.09 kB minified JavaScript advisory; service worker precached 19 files (991.42 KiB) | Pass with bundle-size advisory |
| Ruff and strict mypy | Clean | Ruff passed on changed API files; strict mypy passed for `intake.py` | Pass |
| Lighthouse | Performance ≥85; accessibility ≥95; best practices ≥95; CLS ≤0.1 | Six runs: kiosk performance 85–86, demo status 94–95, accessibility 100, best practices 96, maximum CLS 0.0688 | Pass |

The browser reconnect test uses a mocked HTTP replay to isolate client behavior. The separate PostgreSQL/Redis integration test exercises the real API and confirms persistence across an app restart after the first successful operation.

## 4. Commands run

- `docker compose run ... python -m pytest services/api/tests/unit/test_intake.py services/api/tests/integration/test_offline_intake.py -q` — 10 passed against the Compose PostgreSQL and Redis services.
- `npm test -- --run` — 36 passed (12 files).
- `npm run typecheck`, `npm run lint`, and `npm run check-locales` — passed; 403 keys per language.
- `npm run build` — passed; service worker generated with 19 precache entries.
- `npm run e2e -- e2e/phase-12-offline.spec.ts` — 1 passed; axe found no serious or critical violations.
- `npm run e2e:pwa` — 1 passed; the offline intake route loaded from the cached shell and cached prompt audio played while offline.
- `docker compose run ... python -m ruff check ...` — passed.
- `docker compose run ... python -m mypy --strict services/api/app/api/v1/intake.py` — passed.
- `npm run lighthouse:ci` — all six runs passed the configured thresholds; kiosk performance 85–86, status page 94–95, accessibility 100, best practices 96, maximum CLS 0.0688.

## 5. Screenshots and artifacts

- `apps/web/e2e/phase-12-offline.spec.ts` — offline form, danger warning, encrypted outbox, replay, and axe coverage.
- `services/api/tests/integration/test_offline_intake.py` — PostgreSQL/Redis replay across an API restart.
- `apps/web/.lighthouseci/` — Lighthouse reports and summary, if the configured run completes.

## 6. How the human tests this phase

1. Activate a kiosk while online and open `/kiosk/offline-intake`.
2. Disconnect the browser, fill the short form with synthetic information, select a danger tile, and verify the nurse instruction appears immediately.
3. Save the form and verify a provisional `L-...` receipt and pending-sync message appear.
4. Restore connectivity and verify the server token replaces the provisional receipt and the pending count clears.
5. For server replay recovery, run the PostgreSQL/Redis integration test. It replays the identical idempotency key after a new API lifespan and checks that one visit exists.

## 7. Known issues and decisions

- Prescription upload and extraction remain optional stretch work and were not implemented.
- The production JavaScript chunk is 574.09 kB, above Vite's 500 kB advisory threshold.
- Redis idempotency retains the completed response for 24 hours. The integration test covers a lost client confirmation followed by an API restart after successful processing; it does not kill the process mid-operation between separate database commits.
- Clinical consent and danger-sign text retain the existing clinician-review status and require review before real-world use.

## 8. Next phase preview

Phase 13 hardens the project with security scans, abuse tests, dependency pinning/SBOM, infrastructure and load testing, chaos recovery, observability, and backup/restore documentation.
