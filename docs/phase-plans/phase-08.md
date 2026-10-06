# Phase 8 — Voice input

**Status:** In progress on `phase-08-voice`, branched from merged Phase 7 commit `16d4952`. Step 8.1 is complete. Sarvam's current Realtime STT docs and provider decision are recorded in ADR 003. No real API key was supplied; the implementation stays on Mock until one is configured.

## Work order

1. [x] **8.1 Provider docs and decision:** check the current official Sarvam STT docs; record endpoints, auth, models, audio formats, and supported app languages. Keep Mock active because no provider key was supplied.
2. [x] **8.2 STT adapters:** define `STTProvider`, fixture-driven `MockSTT`, the Sarvam realtime and clip adapters, provider chain, circuit breaker, and metrics.
3. [x] **8.3 Audio WebSocket:** authenticated endpoint, per-kiosk request limits, frame size limit, 60-second utterance cap, idle timeout, backpressure, and no-audio-storage default.
4. [x] **8.4 Browser capture:** microphone permission handling, 16 kHz mono PCM16 AudioWorklet frames, voice activity detection, level meter, and 60-second cap.
5. [ ] **8.5 Interview integration:** voice orb states, live transcript, final submission as `input_mode=voice`, tap-to-edit, and low-confidence review prompt.
6. [ ] **8.6 Mixed language:** pass UI language as the recognition language, accept code-mixing, and preserve the transcript's original script without translation.
7. [ ] **8.7 Safe fallback:** switch to touch and typing within three seconds on provider failure, absent partials, or open circuit; keep nurse-assisted entry available.
8. [ ] **8.8 Evaluation:** document the 80-utterance consenting-speaker recording script and implement Mock CI evaluation. Keep recordings ignored and out of Git.
9. [ ] **8.9 Verification:** protocol/provider tests and Chromium fake-audio Playwright coverage.

## Step 8.1 verification

- Official Sarvam documentation checked on 2026-10-06. See [ADR 003](../DECISIONS.md#adr-003--sarvam-real-time-speech-recognition) for source links and endpoint choices.
- The Realtime endpoint is selected because it streams partial transcripts; the legacy endpoint only returns final text. The selected PCM16 mono 16 kHz format matches the planned browser worklet.
- No real Sarvam API key was supplied. No live request or audio recording was attempted; Mock remains the default.

## Step 8.2 verification

- Added the provider contract, Mock and Sarvam adapters, a five-failure/30-second circuit breaker, and aggregate counters that exclude audio, transcript text, and patient identifiers.
- Live provider errors produce an unavailable result for the touch/typing flow; they never substitute synthetic Mock text.
- Seven protocol tests pass, Ruff passes for the STT package and tests, and strict mypy passes for the STT package. Full backend tests on Windows report **356 passed, 13 failed, 3 errors, 1 skipped**; the failures are existing environment gates (Postgres/Redis integration services unavailable, temporary-directory permission errors, and WeasyPrint native libraries missing).
- Full app/test Ruff lint and strict app mypy pass. Repository-wide `ruff format --check` identifies five previously unformatted files outside the Phase 8 change; all Phase 8 files are formatted.

## Step 8.3 verification

- Added `/ws/intake/{visit_id}/audio`: it requires the patient token for that exact visit, a consented live session, and matching session language before accepting audio.
- The socket caps each PCM16 frame at 8,192 bytes, limits utterances to 60 seconds and idle periods to 15 seconds, rate-limits connections, and atomically caps each kiosk at four concurrent audio sockets. It relays transcript events only; audio is not persisted.
- Three socket tests cover successful streaming and slot release, pre-consent rejection, and kiosk capacity enforcement. Combined provider and socket tests: **10 passed**. Full app strict mypy and full app/test Ruff checks pass.

## Step 8.4 verification

- Added browser microphone capture through AudioWorklet. It converts mono input to 16 kHz PCM16, sends 100 ms frames in memory, reports the input level, ends after 1.2 seconds of silence, and caps capture at 60 seconds.
- Microphone denial and unsupported browser APIs produce stable error codes for localized UI handling. Capture stops the media tracks and closes the audio context; the code does not write audio to browser storage.
- Capture unit tests: **2 passed**. ESLint and TypeScript project build pass.

## Gate

Protocol/provider tests pass; the voice Playwright flow passes; touch fallback appears within three seconds; no audio persists when `STORE_AUDIO=false`. Report live WER, keyword recall, and latency if a real provider key and consenting recordings are available. Do not claim live-provider accuracy without measuring it.

## Manual verification

Use synthetic cases. Speak in Hindi, Marathi, and English and inspect the live text. Try a noisy room, deny microphone permission, and disconnect the network during an utterance; each failure must return to touch and typing. Confirm audio is absent from persistent storage. Live Sarvam checks require a configured key and consenting speakers.

## Next phase preview

Phase 9 adds constrained language-model assistance for question selection, extraction, and red-flag review, with deterministic fallbacks and explicit safety checks.
