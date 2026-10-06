# Architecture decision records

## ADR 001 — Locked technology stack

- **Status:** accepted for the MVP
- **Decision:** Use the stack specified in Part C of `AGENT_PLAYBOOK.md`: React/Vite/TypeScript for the web client; Python/FastAPI/Pydantic/SQLAlchemy for the API; PostgreSQL and Redis; Docker Compose for local infrastructure.
- **Reason:** Keep one documented, testable path through the MVP and preserve module boundaries for later scaling.
- **Consequences:** Do not introduce another framework, database, or major library without recording an ADR and obtaining the human owner's approval.

## ADR 002 — MVP implementation versus production target

- **Status:** accepted for the MVP
- **Decision:** Implement the MVP substitutions documented in Part C2 of `AGENT_PLAYBOOK.md` behind the same module boundaries as the production target.
- **Reason:** A small team can build and demonstrate the product locally while retaining a clear upgrade path.
- **Consequences:** Docker Compose and in-app JWT are MVP choices; real deployment and hospital integrations are out of scope.

## ADR 003 — Sarvam real-time speech recognition

- **Status:** accepted for the Phase 8 MVP; live-provider use remains disabled until a real API key is configured.
- **Decision:** Use Sarvam's Realtime Speech-to-Text WebSocket for streaming English, Hindi, and Marathi input. Connect to `wss://api.sarvam.ai/speech-to-text-realtime/ws` with the `api-subscription-key` header, model `saaras:v3-realtime`, `stream_type=fast`, the selected BCP-47 language (`en-IN`, `hi-IN`, or `mr-IN`), `mode=transcribe`, `encoding=linear16`, and `sample_rate=16000`. Send mono PCM16 in base64 `audio_input` messages; consume partial, final, VAD, session, and structured error events. Preserve the source script and never request translation.
- **Reason:** The current Realtime endpoint emits partial transcript events while the patient is speaking. Sarvam's legacy `/speech-to-text/ws` endpoint emits only final transcripts, so it cannot support the live transcript UI required by Phase 8. The Realtime endpoint accepts mono linear16 at 16 kHz, matching the planned browser capture format.
- **Clip fallback:** For recordings up to 30 seconds, use `POST https://api.sarvam.ai/speech-to-text` with multipart audio, `model=saaras:v4`, `mode=transcribe`, and the same `api-subscription-key` header. REST supports WAV, MP3, AAC, FLAC, OGG, WebM and other documented formats. Longer recordings require Sarvam Batch and remain outside this phase.
- **Language behavior:** Configure `en-IN`, `hi-IN`, or `mr-IN` from the UI language rather than auto-detecting; mixed-language content is preserved in the original script. If a language is genuinely unknown, Realtime supports `language_code=auto`, but that is not used for the fixed-language MVP flow.
- **Fallback and privacy:** Mock STT remains the default. No Sarvam API key was supplied, so no live-provider request or recording verification was run. Audio is not retained; both live and clip paths must keep provider credentials server-side.
- **References checked 2026-10-06:** [Realtime Streaming Speech-to-Text API](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming), [authentication](https://docs.sarvam.ai/api-reference/authentication), and [Saaras models and REST limits](https://docs.sarvam.ai/api/getting-started/models/saaras).
