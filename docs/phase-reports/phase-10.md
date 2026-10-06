# Phase 10 — Read-back, text to speech and patient confirmation (6 October 2026, `phase-10-readback-tts`)

## 1. In plain words

The kiosk builds a short summary from the structured case sheet, lets the patient replay fixed prompts, correct an answer, and confirm the result. Staff can see the confirmation time, corrections, and requests for assistance. Patient-specific speech is generated only on request and is never cached.

## 2. What was built

- Added deterministic English, Hindi, and Marathi read-back templates covering the complaint, duration, severity, symptoms, conditions, medicines, and allergies, capped at 90 words.
- Added Mock, Sarvam Bulbul v3, and Google Cloud TTS adapters with circuit breaking and fallback. Mock remains the default; Google uses server-side ADC.
- Added a hash-based prompt audio generator and complete manifest for 209 prompt IDs in three languages (627 language entries). Unchanged audio is skipped. Mock tone files are accepted for CI.
- Registered the production PWA service worker and precached fixed prompt audio. The offline Playwright flow verifies the prompt is served by the service worker with the browser offline.
- Added patient-authenticated, in-memory read-back audio with `private, no-store` caching; provider failure leaves the text visible.
- Added exact-question correction, reprocessing of the corrected answer, patient confirmation timestamp and correction metadata, and a 90-second assistance flag surfaced in the staff queue and consultation view.
- Added ADR 005 for TTS provider and privacy choices.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---:|---:|---|
| Fixed prompt audio coverage | 100% | 209 IDs × 3 languages = 627 entries | Pass |
| Read-back golden cases | 30 cases | 30 passed; all three languages, word cap and placeholders checked | Pass |
| Correction and red-flag update | Flow passes | Playwright correction reopens the exact question and displays the new urgent alert | Pass |
| Confirmation | Timestamp stored | Repository test verifies timestamp, idempotent response, and confirmation state | Pass |
| Staff confirmation and assistance display | Visible | Staff view tests and queue UI include confirmation, corrections, and assistance status | Pass |
| Idle assistance timeout | 90 seconds | Playwright confirms nurse-help state and assistance request | Pass |
| TTS failure behavior | Text remains, no error | Playwright confirms read-back text remains with no alert | Pass |
| Fixed prompt offline playback | No network dependency | Playwright runs against production PWA, disables browser network, and verifies service-worker response | Pass |
| API tests | 0 failures | 723 passed, 1 warning; 80% total app coverage | Pass |
| Frontend unit tests | 0 failures | 35 passed across 11 files | Pass |
| Browser tests | 0 failures | 4 passed against production build | Pass |
| Ruff and mypy | Clean | Ruff passed; mypy strict passed for 76 API source files | Pass |
| Frontend lint, typecheck, locales, and production build | Clean/succeeds | All passed; locale parity 346 keys per language | Pass |

## 4. Commands run

- `docker compose run ... python -m pytest services/api/tests --cov=services/api/app --cov-report=term-missing -q` — 723 passed; 80% coverage.
- `npm test` — 35 passed (11 files).
- `npm run lint`, `npm run typecheck`, and `npm run check-locales` — passed.
- `npm run build` — passed; PWA precache contains 18 entries. The existing 531.57 kB minified JS chunk warning remains.
- `npm run e2e` — 3 Phase 10 interaction flows passed against the dev server.
- `npm run e2e:pwa` — 1 passed against the production PWA with browser network disabled for playback.
- `uv run ruff check ...` — passed.
- `uv run mypy --strict services/api/app` — passed (76 source files).
- `PYTHONPATH=services/api python services/api/scripts/generate_prompt_audio.py --check` — 209 prompt IDs, 627 language entries, 0 generated.

## 5. Screenshots and artifacts

- `apps/web/public/audio/manifest.json` and `apps/web/src/lib/promptAudioManifest.json` — fixed prompt manifests.
- `apps/web/public/audio/{en,hi,mr}/mock-tone.wav` — offline CI audio fixtures.
- `docs/screenshots/phase-10/s6-summary-after-correction.png` — corrected summary screen.

## 6. How the human tests this phase

1. Complete an intake using synthetic data, change the duration answer, and verify the read-back reflects it.
2. Add an urgent phrase during a correction and verify the alert is shown before confirmation.
3. Confirm the summary, open the staff consultation view, and verify the timestamp and corrections.
4. Leave the summary idle for 90 seconds and verify the nurse-help flag appears in the queue.
5. Install the production PWA and replay a fixed prompt after disconnecting the network.

## 7. Known issues and decisions

- Live Sarvam and Google synthesis was not run; no provider credentials or live voice quality review were part of this handoff. Mock tone assets prove the offline path and manifest completeness, not spoken prompt quality.
- Hindi and Marathi read-back wording still needs native-speaker review; clinical wording still needs clinician review before real-world use.
- Patient-specific generated audio was not cached. TTS failure continues to show the deterministic text summary.
- The production build reports a 531.57 kB minified JavaScript chunk, above Vite's 500 kB advisory threshold.

## 8. Next phase preview

Phase 11 adds opt-in patient notifications, exactly-once outbox delivery, retry/dead-letter handling, and operational analytics. Console notifications remain the default; SMS and WhatsApp providers must stay disabled until explicitly configured.
