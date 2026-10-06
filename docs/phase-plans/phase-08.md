# Phase 8 — Voice input

**Status:** Implementation steps 8.1–8.9 are complete on `phase-08-voice`, branched from merged Phase 7 commit `16d4952`. Sarvam's current Realtime STT docs and provider decision are recorded in ADR 003. No real API key was supplied; Mock remains default and live speech accuracy is unmeasured.

## Work order

1. [x] **8.1 Provider docs and decision:** check the current official Sarvam STT docs; record endpoints, auth, models, audio formats, and supported app languages. Keep Mock active because no provider key was supplied.
2. [x] **8.2 STT adapters:** define `STTProvider`, fixture-driven `MockSTT`, the Sarvam realtime and clip adapters, provider chain, circuit breaker, and metrics.
3. [x] **8.3 Audio WebSocket:** authenticated endpoint, per-kiosk request limits, frame size limit, 60-second utterance cap, idle timeout, backpressure, and no-audio-storage default.
4. [x] **8.4 Browser capture:** microphone permission handling, 16 kHz mono PCM16 AudioWorklet frames, voice activity detection, level meter, and 60-second cap.
5. [x] **8.5 Interview integration:** voice orb states, live transcript, final submission as `input_mode=voice`, tap-to-edit, and transcript review prompt.
6. [x] **8.6 Mixed language:** pass UI language as the recognition language, accept code-mixing, and preserve the transcript's original script without translation.
7. [x] **8.7 Safe fallback:** switch to touch and typing within three seconds on provider failure, absent partials, or open circuit; keep nurse-assisted entry available.
8. [x] **8.8 Evaluation:** document the 80-utterance consenting-speaker recording script and implement Mock CI evaluation. Keep recordings ignored and out of Git.
9. [x] **8.9 Verification:** protocol/provider tests and Chromium fake-audio Playwright coverage.

## Step 8.1 verification

- Official Sarvam documentation checked on 2026-10-06. See [ADR 003](../DECISIONS.md#adr-003--sarvam-real-time-speech-recognition) for source links and endpoint choices.
- The Realtime endpoint is selected because it streams partial transcripts; the legacy endpoint only returns final text. The selected PCM16 mono 16 kHz format matches the planned browser worklet.
- No real Sarvam API key was supplied. No live request or audio recording was attempted; Mock remains the default.

## Step 8.2 verification

- Added the provider contract, Mock and Sarvam adapters, a five-failure/30-second circuit breaker, and aggregate counters that exclude audio, transcript text, and patient identifiers.
- Live provider errors produce an unavailable result for the touch/typing flow; they never substitute synthetic Mock text.
- Eight provider tests pass, Ruff passes for the STT package and tests, and strict mypy passes for the STT package. Mock without an explicit fixture returns unavailable instead of synthetic patient text. Full backend tests on Windows report **356 passed, 13 failed, 3 errors, 1 skipped**; the failures are environment gates (Postgres/Redis integration services unavailable, temporary-directory permission errors, and WeasyPrint native libraries missing).
- Full app/test Ruff lint and strict app mypy pass. Repository-wide `ruff format --check` identifies five previously unformatted files outside the Phase 8 change; all Phase 8 files are formatted.

## Step 8.3 verification

- Added `/ws/intake/{visit_id}/audio`: it requires the patient token for that exact visit, a consented live session, and matching session language before accepting audio.
- The socket caps each PCM16 frame at 8,192 bytes, limits utterances to 60 seconds and idle periods to 15 seconds, rate-limits connections, and atomically caps each kiosk at four concurrent audio sockets. It relays transcript events only; audio is not persisted.
- Three socket tests cover successful streaming and slot release, pre-consent rejection, and kiosk capacity enforcement. Current combined provider and socket tests: **11 passed**. Full app strict mypy and full app/test Ruff checks pass.

## Step 8.4 verification

- Added browser microphone capture through AudioWorklet. It converts mono input to 16 kHz PCM16, sends 100 ms frames in memory, reports the input level, ends after 1.2 seconds of silence, and caps capture at 60 seconds.
- Microphone denial and unsupported browser APIs produce stable error codes for localized UI handling. Capture stops the media tracks and closes the audio context; the code does not write audio to browser storage.
- Capture unit tests: **2 passed**. ESLint and TypeScript project build pass.

## Step 8.5 verification

- Wired microphone permission, connection, recording, processing, review, and fallback states into the patient interview. Partials update the answer field in place so the patient can correct it before submission; voice answers are submitted with `input_mode=voice`.
- Transcripts are always reviewable. Sarvam reports language detection confidence only in adaptive-language mode; because this flow requests a specific language, a missing transcript confidence also asks the patient to review the text instead of pretending language confidence measures transcription accuracy.
- Added matching English, Hindi, and Marathi strings. Interview/audio unit tests: **3 passed**; full frontend suite: **35 passed** across 11 files. Locale parity (331 keys/language), ESLint, and TypeScript project build pass.

## Step 8.6 verification

- The UI's selected `en`, `hi`, or `mr` value is mapped to the matching Sarvam BCP-47 code. The provider and interview pass transcript text through without translation, transliteration, normalization, or script conversion.
- A protocol test verifies a mixed Devanagari/Latin transcript remains byte-for-byte unchanged.

## Step 8.7 verification

- After local speech begins, the kiosk starts a three-second watchdog and resets it when partial text arrives. Provider errors, a missing first partial, or stalled partials close the socket, stop microphone tracks, and leave the answer editable by touch or typing.
- The capture worklet ignores leading silence for VAD, ends after 1.2 seconds of silence after speech, and offers touch fallback after 15 seconds with no detected speech.
- The interview test advances a fake clock by exactly three seconds and confirms the touch/typing fallback message; the provider outage path never supplies made-up text.

## Step 8.8 verification

- Added 80 synthetic reference prompts (30 Hindi, 30 Marathi, 20 English), plus a recording protocol requiring three consenting adult volunteers, quiet/noisy conditions, no patient recordings, and immediate post-evaluation deletion. `eval/stt/audio/` is Git-ignored.
- `eval/stt/evaluate.py` and `make eval-stt` run the deterministic Mock-only CI evaluation. The local run measured **80 cases**, WER **0.0** and exact-keyword recall **1.0** for each language; Mock p95 was below **0.006 ms** on this Windows run. These numbers measure fixture plumbing only, not speech recognition accuracy; live audio WER/latency remain unmeasured because no key or volunteer clips were supplied.
- Metric and fixture tests: **3 passed**. A standard-library WER implementation is used so the synthetic CI evaluation adds no new runtime dependency.

## Step 8.9 verification

- Provider, audio-socket, and evaluation tests pass; Chromium Playwright fake-audio streaming/editing and microphone-denial flows pass **2/2**.
- Full project frontend tests, accessibility/lint/type checks, and production build are recorded in the Phase 8 report.

## Gate

Focused protocol/provider and evaluation tests pass; the voice Playwright flow passes; touch fallback appears within three seconds; no audio persists when `STORE_AUDIO=false`. The local full backend suite and hosted CI gate are still pending resolution: this Windows run had environment-related failures, and the branch has not been pushed for hosted CI. See the [Phase 8 report](../phase-reports/phase-08.md) for exact results. Report live WER, keyword recall, and latency only if a real provider key and consenting recordings are available; do not claim live-provider accuracy without measuring it.

## Manual verification

Use synthetic cases. Speak in Hindi, Marathi, and English and inspect the live text. Try a noisy room, deny microphone permission, and disconnect the network during an utterance; each failure must return to touch and typing. Confirm audio is absent from persistent storage. Live Sarvam checks require a configured key and consenting speakers.

## Next phase preview

Phase 9 adds constrained language-model assistance for question selection, extraction, and red-flag review, with deterministic fallbacks and explicit safety checks.
