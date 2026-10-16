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
- **Compatibility, resilience, and cost:** The pinned Google SDK currently requires `websockets<17`; Uvicorn 0.54 requires websockets 17.2. Keep Uvicorn below 0.54 until the SDK supports websockets 17, while retaining Phase 8's websocket support on websockets 16. Bound each request to 2.5 seconds, allow at most one jittered retry for retryable failures within that total deadline, enforce a hard 30-call per-session cap and 1024-token output ceiling, and use the deterministic path when AI is globally disabled, a hospital has not opted in, the circuit is open, or a response fails validation. Provider failure is not shown to the patient.
- **Privacy:** `MockLLM` is the test/default provider. Gemini is opt-in via server configuration and a tenant-scoped hospital-admin setting, disabled for hospitals by default. Redact PII before every outbound request, do not log prompts/responses, and cache only fixed allow-listed generic copy. Only safe aggregate latency/token/error counters may be recorded.
- **References checked 2026-10-06:** [Gemini model catalog](https://ai.google.dev/gemini-api/docs/models), [Gemini 3.8 Flash model card](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash), [structured outputs](https://ai.google.dev/gemini-api/docs/structured-output), [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/), [SDK release 2.28.0](https://github.com/googleapis/python-genai/releases/tag/v2.28.0), and [SDK websockets compatibility](https://github.com/googleapis/python-genai/issues/2835). Model availability and schema subsets are provider-controlled; the application must continue to validate every value.

## ADR 005 — Multilingual text-to-speech

- **Status:** accepted for Phase 10; Mock remains the default.
- **Decision:** Use Sarvam REST `POST /text-to-speech` with Bulbul v3 as the primary Indian-language adapter and Google Cloud Text-to-Speech as a secondary adapter using Application Default Credentials. Return audio bytes through a small provider interface with a short circuit breaker and fall back to the Mock tone when live providers fail.
- **Read-back privacy:** Fixed UI copy is generated into language-specific static audio and precached by the web app. Patient-specific read-back audio is synthesized only on a patient-authenticated request, held in memory, and returned with `Cache-Control: private, no-store`; the application never writes it to disk or logs the text. `READBACK_LLM` remains off; templates build read-back text deterministically.
- **Provider details checked 2026-10-06:** Sarvam REST accepts up to 2,500 characters, takes `api-subscription-key`, BCP-47 `language_code`, model and speaker settings, and returns base64 audio in JSON. Google Cloud REST uses `POST /v1/text:synthesize`, accepts input, voice and audio configuration, and returns base64 `audioContent`; the adapter uses OAuth/ADC rather than putting credentials in a URL.
- **References:** [Sarvam TTS REST API](https://docs.sarvam.ai/api-reference/text-to-speech/convert), [Sarvam API choice guidance](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/which-api-to-use), [Google synthesize method](https://docs.cloud.google.com/text-to-speech/docs/reference/rest/v1/text/synthesize), and [Google Cloud TTS authentication](https://docs.cloud.google.com/text-to-speech/docs/authentication).

## ADR 006 — Temporary waiver: `mcp` advisory in the CI-only Semgrep lock

- **Status:** accepted on 16 Oct 2026 as a temporary waiver. Remove it as soon as a Semgrep release allows `mcp` 1.30.0 or later.
- **Finding:** `pip-audit` reports GHSA-84m7-p3x7-pcfv (CVE-2026-59951, published 15 Oct 2026) for `mcp` 1.29.0 in `services/api/requirements-security.lock.txt`. The advisory is unbounded session growth (memory exhaustion) in MCP servers that serve the Streamable HTTP transport in stateful mode. It is fixed in `mcp` 1.30.0 and 2.2.0.
- **Why it cannot be fixed now:** that lock holds only the Semgrep scanner, which CI installs on Linux. Every Semgrep release pins `mcp==1.29.0` exactly, including 1.181.0 (14 Oct 2026, the newest when checked). Forcing a newer `mcp` breaks CI's hash-locked `pip install` with a dependency conflict, or needs `--no-deps` against Semgrep's declared requirements.
- **Why the risk is acceptable:** CI runs only `semgrep scan` on the API source, for a few minutes. Semgrep starts no MCP server there, so nothing serves the affected transport. `mcp` is not in the application's lock (`services/api/requirements.lock.txt`), its Docker images or any deployment.
- **Decision:** CI audits the Semgrep lock with `--ignore-vuln` for exactly this advisory (both of its IDs). The application lock is still audited with no exceptions, and any other advisory in the Semgrep lock still fails CI.
- **Review:** when Semgrep's `mcp` pin moves, regenerate the lock with the command in its header and delete the two `--ignore-vuln` arguments from `.github/workflows/ci.yml`.
