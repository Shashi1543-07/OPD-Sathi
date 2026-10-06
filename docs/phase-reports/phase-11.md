# Phase 11 — Notifications and analytics (7 October 2026, `phase-11-notify-analytics`)

## 1. In plain words

Patients can opt in to queue updates, and administrators can review hospital-scoped operational summaries and download a CSV. Queue notices use safe templates and durable, retryable delivery. Analytics are served from a daily materialized rollup.

## 2. What was built

- Added an explicit, default-off per-visit SMS consent and English, Hindi, and Marathi templates for token issuance and queue position thresholds.
- Added console, Twilio SMS, and WhatsApp Cloud providers. Console remains the default; external providers require explicit configuration.
- Added durable notification deduplication, queue threshold detection, exponential retry and dead-letter handling, and a five-per-recipient-per-hour cap. Raw phone numbers are not stored in notifications.
- Added a restricted analytics worker role and a daily hospital/department materialized rollup, refreshed concurrently every five minutes.
- Added audited, hospital-scoped summary and timeseries APIs and a streamed CSV export with spreadsheet-formula escaping.
- Replaced the reports placeholder with localized KPIs, daily patient trend, language mix, department load, an editable three-minute history baseline, and CSV download.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---:|---:|---|
| Notification deduplication, opt-in, thresholds, retry and rate cap | Covered | Focused notification and analytics API tests: 25 passed | Pass |
| Rollup arithmetic, refresh, and hospital isolation | Hand-computed fixture; refresh and tenant scope | PostgreSQL integration fixture passed against Compose database | Pass |
| Analytics CSV streaming | 100,000 rows; under 100 MB peak | 100,000 rows streamed; 0.13 MB peak traced Python memory | Pass (unit stream fixture) |
| Summary p95 on 100,000 visits | At most 100 ms | 1.88 ms p95, 30 measured runs against the seeded 100,000-visit hospital | Pass |
| Dashboard accessibility | 0 serious or critical axe findings | 0 serious or critical; 1 Phase 11 browser test passed | Pass |
| Frontend unit tests | 0 failures | 36 passed across 12 files | Pass |
| Frontend typecheck, lint, and locale parity | Clean | Typecheck and lint passed; 374 keys per language | Pass |
| Production build | Succeeds | Passed; 1,705 modules; 554.14 kB minified JS advisory | Pass with existing bundle-size advisory |
| Ruff and strict mypy | Clean | Both passed for changed Phase 11 Python files | Pass |

The hand-computed fixture and live PostgreSQL integration passed. The 100,000-row CSV memory result remains a unit stream fixture measurement, while the summary latency measurement used the live seeded Compose database.

## 4. Commands run

- `.venv/Scripts/python.exe -m pytest services/api/tests/unit/test_analytics.py services/api/tests/integration/test_analytics_rollup.py services/api/tests/unit/test_notification_queue.py services/api/tests/unit/test_notification_providers.py -q` — 24 passed, 1 skipped.
- `docker compose run ... python -m pytest services/api/tests/unit/test_analytics.py services/api/tests/integration/test_analytics_rollup.py services/api/tests/unit/test_notification_queue.py services/api/tests/unit/test_notification_providers.py -q` — 25 passed against the Compose PostgreSQL database.
- `docker compose run ... python services/api/scripts/benchmark_analytics.py --hospital-id 019b76da-a800-788a-852f-3dbb37d8bdd5 --iterations 30` — summary query p95 1.88 ms on the seeded hospital.
- `.venv/Scripts/python.exe -m pytest services/api/tests/unit/test_analytics.py -q -s` — 4 passed; 100,000-row CSV stream peaked at 0.13 MB traced memory.
- `npm test -- --run` — 36 passed (12 files).
- `npm run typecheck`, `npm run check-locales`, and `npm run lint` — passed; 374 locale keys per language.
- `npm run build` — passed; the 554.14 kB minified JS bundle exceeds Vite's 500 kB advisory threshold.
- `npm run e2e -- e2e/phase-11.spec.ts` — 1 passed; axe reported 0 serious or critical issues.
- `.venv/Scripts/python.exe -m ruff check ...` and `.venv/Scripts/python.exe -m mypy --strict ...` — passed.

## 5. Screenshots and artifacts

- `docs/screenshots/phase-11/admin-analytics.png` — reports dashboard and accessibility verification.
- `services/api/scripts/benchmark_analytics.py` — database-backed 100,000-visit summary latency benchmark; requires a seeded PostgreSQL database and hospital ID.

## 6. How the human tests this phase

1. Configure only synthetic patient details, leave SMS consent off, and verify no notification is queued.
2. Enable consent, enqueue a synthetic visit, and watch the console provider report token issuance and threshold messages once each.
3. Sign in as an administrator and compare the dashboard totals and language counts to a small synthetic database fixture.
4. Download the 30-day CSV and confirm the exported rows are hospital-scoped.

## 7. Known issues and decisions

- Live Twilio and WhatsApp delivery was not run; external providers remain disabled unless configured.
- The production bundle produces a 554.14 kB minified JS chunk, above Vite's 500 kB advisory threshold.

## 8. Next phase preview

Phase 12 adds a kiosk short form that can be completed offline and safely replayed to the server exactly once when connectivity returns. Prescription upload remains a stretch goal.
