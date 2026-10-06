# Phase 08 — Voice input (6 October 2026, `phase-08-voice`)

## 1. In plain words

Patients can speak an answer during the intake interview. The kiosk shows the words as they arrive, keeps them editable, and records that the answer came from voice. If permission, the connection, or partial transcription fails, the microphone stops and touch or typing remains available. Audio is streamed for transcription and is not saved by the application. Mock remains the default because no Sarvam key was provided.

## 2. What was built

- **8.1–8.2:** Recorded the current Sarvam decision; added a provider contract, fixture-driven Mock, realtime/clip adapters, factory, circuit breaker, and transcript-free aggregate metrics. Mock without a configured fixture reports unavailable instead of inventing patient text.
- **8.3:** Added a consent-gated patient audio WebSocket with visit-token checks, 8,192-byte frame limit, 60-second utterance limit, 15-second idle/no-speech handling, kiosk connection rate limit, and four-socket concurrency limit.
- **8.4–8.5:** Added a mono 16 kHz PCM16 AudioWorklet, VAD, level meter, microphone permission states, live editable transcript, transcript review, and `input_mode=voice` submission. English, Hindi, and Marathi strings are present.
- **8.6–8.7:** Preserve mixed-script transcript text without app translation or transliteration. A three-second watchdog returns to touch/typing after detected speech if partials stop.
- **8.8–8.9:** Added 80 synthetic reference prompts, a consent and deletion protocol, Mock CI evaluation, provider/socket tests, and Chromium fake-audio and permission-denial tests.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---:|---:|---|
| Provider, socket, and evaluation unit tests | 0 failed | 15 passed | Pass |
| Frontend unit tests | 0 failed | 35 passed (11 files) | Pass |
| Phase 8 Playwright flows | 0 failed | 2 passed | Pass |
| ESLint, Ruff, strict mypy, TypeScript, locale parity | Clean | All passed; 331 keys per language | Pass |
| Production web build | Succeeds | Built successfully; main JS 491.74 kB (152.76 kB gzip) | Pass |
| Audio persistence | None by default | No audio writes; no recordings in Git; `eval/stt/audio/` ignored | Pass |
| Synthetic Mock evaluation | 80 cases, 30 hi / 30 mr / 20 en | 80; WER 0.0 and keyword recall 1.0 for each language | Pass, fixture plumbing only |
| Live speech WER / keyword recall / latency | Report only with key and consenting audio | Not measured; no key or recordings supplied | Not measured |
| Full backend suite on this Windows host | 0 failed or errored | 364 passed, 13 failed, 3 errors, 1 skipped | Local environment gate failed |

The 13 full-suite failures are existing local infrastructure limitations: Postgres/Redis integration services are unavailable and WeasyPrint cannot load its native `libgobject` dependency on this Windows host. Three test setups also hit permission errors under the shared pytest temporary directory. The Phase 8 provider, socket, and evaluation tests themselves pass. Hosted CI has not run for this unpushed phase branch, so the full phase gate is still pending.

The Mock WER is expected to be zero because the adapter returns the exact configured text fixture. It is not a claim about recognition accuracy. The local Mock p95 values were 0.0056 ms for English, 0.0024 ms for Hindi, and 0.0018 ms for Marathi; they measure only fixture processing.

## 4. Commands run

- `ruff check services/api/app services/api/tests services/api/scripts eval/stt` — passed.
- `mypy --strict services/api/app` — passed.
- `pytest services/api/tests/unit/test_stt_providers.py services/api/tests/unit/test_audio_socket.py services/api/tests/unit/test_stt_eval.py -q` — **15 passed** across the provider/socket and evaluation runs.
- `vitest run` — **35 passed**.
- `eslint .`, `tsc -b --pretty false`, and `npm run check-locales` — passed.
- `npm run build` — passed.
- `playwright test e2e/phase-08.spec.ts` — **2 passed**.
- `python eval/stt/evaluate.py` — **80 cases**, live audio false; per-language WER 0.0 and exact keyword recall 1.0.
- Full backend `pytest services/api/tests -q` — **364 passed, 13 failed, 3 errors, 1 skipped** for the host issues above.

## 5. Screenshots and artifacts

- [Phase 8 implementation plan](../phase-plans/phase-08.md)
- [STT provider decision, ADR 003](../DECISIONS.md#adr-003--sarvam-real-time-speech-recognition)
- [80 synthetic evaluation prompts](../../eval/stt/fixtures.jsonl)
- [Volunteer recording and deletion protocol](../../eval/stt/RECORDING.md)

No volunteer audio was recorded. No live request was made.

## 6. How to test this phase

1. Start the API and web app with the default Mock configuration, then open `/kiosk/intake` using a synthetic patient session.
2. Select a text question and tap the microphone. Without a Sarvam key, the kiosk should offer touch/typing instead of displaying invented transcript text.
3. Deny microphone permission and confirm that the permission message appears and the answer field remains enabled.
4. For a live-provider check, configure `STT_PROVIDER=sarvam` and `SARVAM_API_KEY` in a local environment, use a synthetic visit and consenting speaker, grant microphone access, speak in the selected language, edit the transcript, and submit. Verify the saved interview turn is marked `input_mode=voice` and no audio file was created.
5. Disconnect the provider after speech starts. Touch/typing should return within three seconds.

## 7. Known issues and decisions

- No API key or volunteer audio was available; live Sarvam WER, keyword recall, and latency remain unmeasured.
- The entire backend suite cannot pass on this Windows host because its database/cache services, pytest temp directory, and WeasyPrint native libraries are unavailable. Run hosted CI before treating the phase gate as green.
- Native-speaker review of Hindi and Marathi transcripts remains necessary before real-world use. Phase 4 translation and Phase 6 clinical reviews also remain pending.

## 8. Next phase preview

Phase 9 adds constrained language-model assistance for question selection, extraction, and red-flag review. The project playbook requires the human to reply `approve phase 8` before Phase 9 begins.
