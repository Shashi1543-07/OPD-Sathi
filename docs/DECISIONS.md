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
- **Amendment, 23 Oct 2026:** pip-audit reports CVE-2026-114603 for `opentelemetry-api`, fixed in 1.45.1. The application lock now uses 1.45.1. The Semgrep lock cannot: every Semgrep release, including 1.182.1 (the newest on 23 Oct 2026), requires `opentelemetry-api~=1.37.0`, which Semgrep uses only for its own optional telemetry during the scan. The same reasoning applies as for `mcp`: the package exists only in the CI-only scanner lock, never in the application, its images or any deployment. CI waives exactly this advisory for the Semgrep lock (`--ignore-vuln CVE-2026-114603`); the application lock is audited with no exceptions. Remove the waiver when Semgrep allows `opentelemetry-api` 1.45.1 or later.

## ADR 007 — Production configuration: an overlay, secrets as files, HTTPS in the existing nginx

- **Status:** accepted on 22 Oct 2026 (Phase 7, tasks 7.1.1, 7.2.1, 7.3.7).
- **Decision:** Production runs the same images as development with `docker-compose.prod.yml` laid over `docker-compose.yml`. It sets `APP_ENV=production`, publishes only ports 443 and 80 (redirect), hardens every container (no new privileges, capabilities dropped, read-only root filesystems, resource limits, rotated logs, `restart: always`) and reads every secret from a file in a root-only directory (`/etc/opd-saathi/secrets`) mounted as a Compose secret. The API's settings accept `<NAME>_FILE` for any setting; the plain variables are blanked, so `docker inspect` and `ps` show no secret. Redis receives its password through a generated config file on a private tmpfs, not its command line. HTTPS terminates in the web container's nginx (now `nginx-unprivileged`, uid 101), with Mozilla's "intermediate" TLS profile.
- **Reason:** ADR 001 rules out a new proxy or orchestrator. One set of images keeps what is tested and what runs identical; the overlay changes only how it is run.
- **Consequences:** Installation on Linux uses `scripts/setup-hospital.sh` and a systemd unit; Windows keeps `setup-hospital.ps1`. A CI job starts the stack with the overlay and checks it refuses development values (task 7.1.2).

## ADR 008 — Time-based one-time codes for staff sign-in (`pyotp`)

- **Status:** accepted on 22 Oct 2026 by the owner (Phase 7, task 7.3.2).
- **Decision:** Use `pyotp` (RFC 6238 TOTP, SHA-1, 6 digits, 30-second step, one step of clock drift either way) for a second sign-in step. Administrators must enrol; doctors and nurses may (the hospital decides whether to require it, section 6 of the Phase 7 plan). The TOTP secret is stored encrypted with `APP_DATA_KEY`, bound to the user. Ten single-use recovery codes are shown once and stored as Argon2id hashes. A code cannot be used twice (the last accepted time step is stored). Enrolment, use, recovery and reset are audited. The break-glass path is `python -m app.cli reset-mfa`, run on the server, which is audited and requires the operator's name.
- **Reason:** A small, dependency-free library implementing a standard every authenticator app supports; writing TOTP by hand would be new security-critical code without benefit.
- **Consequences:** Staff need an authenticator app on a phone. Losing both the phone and the recovery codes needs an administrator (or, for the last administrator, the server operator) to reset it.

## ADR 009 — CI-only security scanners: Trivy, OWASP ZAP, Schemathesis, cosign

- **Status:** accepted on 22 Oct 2026 by the owner (Phase 7, tasks 7.4.3–7.4.5, 7.14.3).
- **Decision:** Trivy scans the repository and the built images on every pull request and fails on a high or critical finding without a written waiver in `.trivyignore` (each with a reason and a review date, as in ADR 006). OWASP ZAP (baseline and API scan from the OpenAPI file) and Schemathesis (stateful API fuzzing) run nightly against the production-mode stack. Release images are signed with cosign (keyless, GitHub OIDC) and carry a CycloneDX SBOM. All four run only in GitHub Actions, pinned by version or digest; none enters the application or its images.
- **Reason:** These close the gaps in finding F11 with standard, widely used tools; running them only in CI keeps the product unchanged.
- **Consequences:** Nightly jobs need the production smoke stack (task 7.1.2). A finding blocks merging until fixed or waived in writing.

## ADR 010 — Metrics and alerts: `prometheus-client`, Prometheus and Alertmanager

- **Status:** accepted by the owner: `prometheus-client` on 22 Oct 2026, the Prometheus server and Alertmanager (without Grafana) on 23 Oct 2026 (Phase 7, tasks 7.9.1 and 7.9.2).
- **Decision:** The API exports metrics with `prometheus-client` in multiprocess mode (every Gunicorn process writes to a private tmpfs; `/metrics` adds them up). The worker serves its own metrics on port 9101 and measures what the alerts need without extra exporters: patients waiting per department, the oldest unapplied queue event, unsent messages, the oldest unacknowledged urgent alert, silent kiosks, whether PostgreSQL and Redis answer, free space on the data disk, the expiry of the certificate as served, and the last backup. Labels are route templates, department codes and outcomes, never patient data. Prometheus (30 days of history) and Alertmanager run in the production overlay on the private network only; nginx refuses `/metrics` from outside. Alertmanager sends email through the hospital's mail server and, optionally, Telegram. Rules live in `infra/monitoring/alerts.yml`, each pointing to a runbook, and are unit-tested with `promtool` in CI.
- **Reason:** Request metrics were per process and nothing told a person when something failed (finding F7). Prometheus and Alertmanager are the standard, small, file-configured pair; dashboards (Grafana) were judged not worth another component to secure for a hospital server.
- **Consequences:** Two more containers (about 1.3 GB of memory at most). Hospital IT must give a mail account for alerts. Without dashboards, investigation uses Prometheus's own page through an SSH tunnel and the runbooks.

## ADR 011 — Point-in-time backups with pgBackRest

- **Status:** accepted on 22 Oct 2026 by the owner (Phase 7, tasks 7.7.2, 7.7.3, 7.7.5).
- **Decision:** PostgreSQL runs from our image (`infra/postgres/Dockerfile`: the pinned upstream image with pgBackRest from the PostgreSQL project's repository) and archives every WAL segment with `pgbackrest archive-push`, closing a segment at least every 5 minutes. A `backup` service (same image, PostgreSQL's user, read-only data directory) takes a full backup weekly and a differential nightly, keeping two full backups. The repository is encrypted with AES-256 under its own passphrase (a secret file, escrowed with the data keys, never a data key itself). A second repository off-site (S3-compatible or a mounted NAS) is configured with `PGBACKREST_REPO2_*`. `scripts/restore-drill.sh` restores to a named restore point in a throwaway database, compares every table's row count and decrypts every encrypted value; it runs in the production smoke test on every change and quarterly on each server.
- **Reason:** A nightly `pg_dump` could lose a day of records and was never restored automatically (finding F8). pgBackRest is the standard tool for continuous archiving, encryption, retention and verification of PostgreSQL, and needs no new database or service beyond a small container.
- **Consequences:** The PostgreSQL image is built locally (and scanned like the others). Restoring needs the escrowed backup passphrase and, to read records, the data keys. `scripts/backup.sh` (logical dump) stays as a second copy before upgrades.

## ADR 012 — No separate error-tracking service for v1

- **Status:** decided on 23 Oct 2026 by the OPD Saathi team (Phase 7, task 7.9.4); revisit after the pilot.
- **Decision:** No GlitchTip or Sentry. Errors are found through the structured logs (kept 180 days in the server's journal, without patient data), the `ApiErrorRateHigh` and `WorkerJobFailing` alerts, and the request id every error response carries (staff can quote it; `journalctl ... | grep <request id>` finds the trace).
- **Reason:** An error tracker is another service to install, secure and keep patched on each hospital server, and stack traces risk carrying patient data off the request path; the alerts already tell a person when errors rise, and one server per hospital keeps logs searchable in place.
- **Consequences:** No grouping of similar errors or release-by-release error trends; second-line support reads the journal when an alert or a staff report comes in. A multi-hospital hosted service would revisit this.

## ADR 013 — No PgBouncer yet; a connection budget instead

- **Status:** decided on 24 Oct 2026 by the owner (Phase 7, task 7.8.4); revisit when the trigger below is reached.
- **Decision:** PostgreSQL is reached directly. Each API process keeps `DB_POOL_MIN` connections and opens up to `DB_POOL_MAX`; the worker and the close-out keep their own small pools. The budget in `docs/SCALING.md` (worst case `WEB_CONCURRENCY x DB_POOL_MAX` plus the worker and maintenance connections) must stay below `max_connections`. The tenant scope is set with `set_config('app.hospital_id', ..., true)` for each transaction, and a test proves that a reused connection carries no scope into the next transaction (`test_tenant_rls.py`), so PgBouncer in transaction mode can be added later without changing the application.
- **Reason:** The load tests met every target on one server with direct connections (12 to 17 connections at the district-hospital peak, `docs/SCALING.md`); PgBouncer would be another service to install, configure, secure and monitor on each hospital server without a measured need.
- **Trigger to revisit:** more API processes than the budget allows (for example three API replicas, task 7.8.6), a shared district server (task 7.8.10), or connection waits in the metrics. Then: PgBouncer in transaction mode with an ADR amendment, `server_reset_query` empty, and the scope test run through it.

## ADR 014 — Patients do not see their records in OPD Saathi in v1

- **Status:** decided on 24 Oct 2026 by the OPD Saathi team (Phase 7, task 7.11.8); revisit after the pilot.
- **Decision:** Patients see their queue status, can stop AI help and SMS, and get their OPD card; they do not view their history, case sheets or papers in OPD Saathi. Records reach patients through the hospital (printed case sheet on request) and, later, through ABDM, where the patient's ABHA app is the place to see them (tasks 7.10.2 and 7.10.3).
- **Reason:** A printed card with a six-digit PIN is enough to join today's visit to an earlier record at a kiosk staffed by the hospital, but not to open a medical record from any phone: that needs stronger sign-in (a one-time code to a verified number, or ABHA), and both bring their own risks (shared family phones, SIM changes) that the DPDP notice and counsel would need to cover.
- **Consequences:** No patient sign-in to secure in v1. A patient's right to access their data (DPDP Act section 11) is met by the hospital's process, with the audit log showing who read what.

## ADR 015 — No drug-interaction checks until a licensed, reviewed knowledge base is procured

- **Status:** decided on 24 Oct 2026 by the OPD Saathi team (Phase 7, task 7.11.9).
- **Decision:** OPD Saathi does not check medicines for interactions, doses or contraindications, and never asks a language model to. Medicines appear as the patient stated them or as read from a paper a doctor accepted, labelled as such.
- **Reason:** An interaction check is clinical decision support: it needs a maintained, licensed knowledge base, clinical review of what is shown and when, and probably a change in the regulatory classification (task 7.5.11). An unreviewed AI judgement here could do harm in both directions (missed interactions, alert fatigue).
- **Consequences:** Doctors use their own references, as today. Adding checks later needs: a procured knowledge base (with its licence and update process), a clinical safety case and hazard-log entries, sign-off by the clinical lead, and this ADR replaced.

## ADR 016 — Web test coverage with `@vitest/coverage-v8`

- **Status:** accepted on 24 Oct 2026 by the owner (Phase 7, task 7.13.6).
- **Decision:** The web unit tests run with Vitest's own V8 coverage plugin in CI (`npm run test:coverage`). Every file under `apps/web/src` counts, tested or not (the generated API types, the entry point and the tests themselves excluded). The thresholds in `vite.config.ts` are the level reached when the gate was added and are only ever raised.
- **Reason:** The API has had a 95% coverage gate on its core and domain code; the web app had none, so untested screens could grow unnoticed. The plugin is maintained with Vitest, matches its version, and uses the coverage built into Node, with no instrumentation step.
- **Consequences:** A development dependency only; nothing changes in the built app. A change that lowers coverage below the thresholds fails CI until tests are added.
