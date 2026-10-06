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

## ADR 004 — Gemini structured-output provider

- **Status:** accepted for Phase 9; Mock remains the default and live calls remain disabled unless explicitly configured.
- **Decision:** Use `google-genai==2.28.0` and Gemini Developer API. Set `LLM_MODEL=gemini-3.8-flash` as the primary stable Flash model and `LLM_FALLBACK_MODEL=gemini-3.5-flash-lite` as the stable Flash-Lite fallback. Request `application/json` with a JSON Schema response schema, then validate the parsed value again with the application's Pydantic models and semantic evidence checks. Send no tools, patient names, unredacted identifiers, or data not needed for the individual bounded task.
- **Reason:** Google's current model catalog lists Gemini 3.8 Flash as stable and recommends it for new projects; Gemini 3.5 Flash-Lite is the current stable Flash-Lite option. Google documents JSON Schema structured output, while explicitly warning that schema validity does not establish semantic correctness. This supports the provider contract without replacing local validation or deterministic safety rules.
- **Compatibility and resilience:** The pinned Google SDK currently requires `websockets<17`; Uvicorn 0.54 requires websockets 17.2. Keep Uvicorn below 0.54 until the SDK supports websockets 17, while retaining Phase 8's websocket support on websockets 16. Bound each request to 2.5 seconds, allow at most one jittered retry for retryable failures within that total deadline, and use the existing deterministic path when the circuit is open, the per-session cap is reached, the response is malformed, or semantic validation fails. Provider failure is not shown to the patient.
- **Privacy:** `MockLLM` is the test/default provider. Gemini is opt-in via server-side configuration. Redact PII before every outbound request, do not log prompts/responses, and do not cache patient-specific text or output. Only safe aggregate latency/token/error counters may be recorded.
- **References checked 2026-10-06:** [Gemini model catalog](https://ai.google.dev/gemini-api/docs/models), [Gemini 3.8 Flash model card](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash), [structured outputs](https://ai.google.dev/gemini-api/docs/structured-output), [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/), [SDK release 2.28.0](https://github.com/googleapis/python-genai/releases/tag/v2.28.0), and [SDK websockets compatibility](https://github.com/googleapis/python-genai/issues/2835). Model availability and schema subsets are provider-controlled; the application must continue to validate every value.
