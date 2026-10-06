# OPD Saathi — Project TODO

This is the execution checklist derived from [OPD Saathi Agent Build Playbook](OPD_Saathi_Agent_Playbook.md) (Version 1.0, 5 October 2026). The playbook remains the detailed source of truth for designs, schemas, exact test commands, thresholds, and manual test instructions.

## How to use this list

- [ ] Work on one phase at a time. Do not begin Phase N+1 until the human explicitly replies `approve phase N`.
- [ ] For each numbered build step: inspect the relevant playbook section, plan the files and tests, implement that step, run its stated checks, and commit after it is green. Do not weaken or skip a failing check.
- [ ] At each phase end, measure and print every gate result, update the README and `docs/phase-reports/phase-NN.md`, give the human the manual test steps, and stop for approval.
- [ ] Use synthetic data only. Keep clinical questions, red-flag rules, and routing marked `draft_needs_clinician_review` until clinician approval.
- [ ] Preserve the history-taking-only safety rules, privacy controls, mock adapters/fallbacks, and locked stack in the playbook. Record proposed stack changes in `docs/DECISIONS.md` and seek approval before adopting them.

## Before implementation

- [x] Read Parts A–G of the playbook, then consult the relevant later sections and appendices as each phase requires.
- [x] Record the development environment required by Phase 0: Windows with WSL2 or another supported OS, Docker, Node LTS, and Python 3.12. (Ubuntu WSL 2, Docker Desktop, Python 3.12.15, Node 24.21.0, npm 11.19.0, and GNU Make are verified.)
- [x] Review the current workspace and git state; create the playbook's required repository layout without overwriting existing work.
- [x] Create, commit, and push the Phase 0 branch (`phase-00-foundations`; latest CI-green commit `462808d`).

## Phase 0 — Foundations and tooling

- [x] 0.1 Confirm OS and installed tools; document setup (Windows host, Ubuntu WSL 2, Docker Desktop 4.93.0, Engine 29.8.1, Compose 5.5.1, Python 3.12.15, Node 24.21.0, npm 11.19.0, and GNU Make verified).
- [x] 0.2 Create the C4 repository layout, `.gitignore`, `.editorconfig`, and placeholder-only `.env.example` (Gitleaks scan: no findings across 35 project paths).
- [x] 0.3 Add Docker Compose for Postgres, Redis, API, and worker with health checks and pinned image versions (Postgres, Redis, API, and worker all started; Postgres and Redis healthy; API healthy).
- [x] 0.4 Build the FastAPI app/config/logging/error/security/CORS/request-ID and health/readiness foundations (five API tests pass; live `/healthz` and `/readyz` return 200; `/readyz` returns 503 naming Redis during a Redis outage).
- [x] 0.5 Add async SQLAlchemy, Alembic, and the first reversible migration for hospitals and the limited app role (live upgrade → downgrade → upgrade completed successfully).
- [x] 0.6 Build the strict TypeScript React/Vite web shell, routes, i18n files, PWA setup, linting and smoke test (build/lint/typecheck pass; one Vitest smoke test passes).
- [x] 0.7 Generate frontend API types from OpenAPI and add the CI drift check (CI passed on `462808d`).
- [x] 0.8 Add every Make target listed in Appendix B (34 targets are declared; `make up`, `make migrate`, and `make test` verified).
- [x] 0.9 Add GitHub Actions CI for backend, frontend, security scans, and API type drift (CI passed on `462808d`; Linux `pywin32` installation issue fixed in `b5cd827`).
- [x] 0.10 Add README quickstart, ADR 001/002, and phase report directory.
- [x] Gate: CI green on `462808d`; `make test`; clean-checkout setup ≤10 minutes; no secrets found; Redis outage returns 503 naming Redis and readiness recovers; Compose health and migration cycle pass.

## Phase 1 — UI foundation and language selection

Phase 1 is complete on `phase-01-ui-foundation` with the human visual review approved. Phase 2 is complete and merged into that branch as `900d0ef`; see [the Phase 1 report](docs/phase-reports/phase-01.md) and [the Phase 2 report](docs/phase-reports/phase-02.md).

- [x] 1.1 Add design tokens, Tailwind theme, self-hosted fonts, responsive foundations, and contrast checking (12 documented text pairs pass at 4.5:1 or above).
- [x] 1.2 Build the accessible shared component set and component tests (9 Vitest tests pass).
- [x] 1.3 Add the dev component gallery with component states, all three languages, and pseudo-locale.
- [x] 1.4 Add i18n namespaces, in-memory session language state, pseudo-locale, and missing-key checker (73 keys across four namespaces pass parity).
- [x] 1.5 Build the kiosk shell, idle reset, accessibility controls, and audio prompt stub.
- [x] 1.6 Style welcome/language screen and fake-data previews for S2–S7.
- [x] 1.7 Build staff shell/login preview and mock patient status page.
- [x] 1.8 Precache app shell and fonts only.
- [x] 1.9 Add Playwright screenshots, axe, tap-target, pseudo-locale, and keyboard checks (94 passed; 81 screenshots saved).
- [x] 1.10 Configure Lighthouse performance/accessibility/best-practices/CLS checks (6 runs passed; zero high/critical npm audit findings).
- [x] Gate: Phase 1 contrast, accessibility, tap-target, Lighthouse, layout, locale, and bundle-size thresholds pass; human visual review approved.

## Phase 2 — Identity, audit, and rate limiting

Phase 2 is complete and merged into `phase-01-ui-foundation` as `900d0ef`. All six GitHub checks passed; see the [Phase 2 report](docs/phase-reports/phase-02.md). Explicit approval to begin Phase 3 was received; implementation is on `phase-03-data-model` and local gates are complete. GitHub CI and merge remain pending.

- [x] 2.1 Add user/kiosk/token/audit migrations, immutable audit permissions, and tenant RLS (migrations 0002–0003 applied locally; app role cannot update audit rows).
- [x] 2.2 Implement Argon2id and JWT security with the required attack/failure tests (12 security tests pass; core security coverage 98%).
- [x] 2.3 Apply per-request hospital RLS context and prove tenant isolation (two-tenant PostgreSQL integration test passes, including no-scope and secret-hash lookups).
- [x] 2.4 Implement login/refresh/logout, refresh rotation/reuse handling, and lockout (PostgreSQL lifecycle tests pass).
- [x] 2.5 Add role/kiosk/patient auth dependencies and full route-matrix test (all registered routes covered).
- [x] 2.6 Add Redis sliding-window rate limiting, headers, and defined outage behavior (Redis Lua test, 429 headers, fail-closed and fallback tests pass).
- [x] 2.7 Implement one-time kiosk activation and kiosk JWT (wrong and reused activation tests pass).
- [x] 2.8 Add immutable audit helper and admin audit endpoint (app role update/delete denied; pagination and events verified).
- [x] 2.9 Wire typed frontend auth, protected routes, refresh, login, and logout flows (16 Vitest tests; 4 Phase 2 Playwright tests pass).
- [x] 2.10 Finalize CSP/security headers, microphone policy, and request size limits.
- [x] Gate: route-matrix, JWT, tenant-isolation, append-only audit, rate-limit, module coverage, Bandit, web, and dependency-audit checks pass locally; all six target-version GitHub checks, including reversible migrations, passed before PR #1 merged as `900d0ef`.

## Phase 3 — Data model, seeds, and pagination

- [x] 3.1 Create domain tables, indexes, uniqueness rules, and forced RLS policies; upgrade/downgrade/upgrade cycle passes.
- [x] 3.2 Add AES-256-GCM field encryption and HMAC lookup hashing; 100,000 nonce uniqueness and encrypted SQL values verified.
- [x] 3.3 Define and validate case-sheet models; export the JSON schema and check it in CI.
- [x] 3.4 Implement async repositories; keep SQL out of routers.
- [x] 3.5 Add signed keyset pagination and tamper/concurrent-insert tests; a 10,000-row scan remained gap-free with 100 inserts.
- [x] 3.6 Add Redis idempotency and replay/body-mismatch tests.
- [x] 3.7 Add fictional demo seed and reproducible synthetic bulk data.
- [x] 3.8 Add audited, paginated admin endpoints for departments, users, and kiosks.
- [x] 3.9 Measure and save queue query plans at 100,000 visits; `ix_visits_queue` p95 was 1.99 ms locally.
- [x] Gate: reversible migrations, encrypted data/no plaintext leakage, RLS, concurrent pagination, query latency, and 98% combined `app/core` + `app/domain` coverage pass locally. GitHub CI is pending.


Phase 3 implementation is complete locally. See the [Phase 3 report](docs/phase-reports/phase-03.md) and [manual demo steps](docs/phase-plans/phase-03.md). Phase 4 implementation and local gates are recorded in the [Phase 4 report](docs/phase-reports/phase-04.md); remote CI and human phase approval remain pending.

## Phase 4 — Deterministic interview and case sheet (no AI)

- [x] 4.1 Implement multilingual consent capture and enforce consent before intake actions.
- [x] 4.2 Add validated identity and danger-sign capture; record danger events.
- [x] 4.3 Add the multilingual question bank, modules, triggers, and CI validator.
- [x] 4.4 Implement/test the pure deterministic interview engine (maximum 12 questions).
- [x] 4.5 Persist interview state and turns; make answer submission idempotent and sanitize input.
- [x] 4.6 Build draft case sheet/read-back using deterministic templates.
- [x] 4.7 Record patient confirmation and temporary token response.
- [x] 4.8 Wire kiosk S2–S7 for touch-only flow and safe idle reset.
- [x] 4.9 Create 30 golden interviews (10 per language) and evaluation command.
- [x] 4.10 Add multilingual end-to-end flows, danger-tile, back/skip, and accessibility checks.
- [x] Gate: 30/30 golden interviews; engine/property and Playwright checks pass; time/tap/input safety limits pass; interview domain coverage meets threshold. See the measured evidence and remaining human review in the Phase 4 report.

## Phase 5 — Queue, token, and live position

- [x] 5.1 Implement and test queue scoring, including worked example C, B, D, A.
- [x] 5.2 Allocate unique daily department tokens, including database fallback when Redis is down.
- [x] 5.3 Add atomic Redis queue operations and live delta publishing.
- [x] 5.4 Add idempotent outbox relay and Redis outage recovery.
- [x] 5.5 Implement queue rebuild from the database.
- [x] 5.6 Replace stub confirmation with token/queue creation and temporary routing defaults.
- [x] 5.7 Add patient status, SSE, ETA, micro-cache, and visit state machine.
- [x] 5.8 Add staff queue actions and authenticated WebSocket snapshot/delta protocol.
- [x] 5.9 Wire token/QR/print slip, patient live status, and staff queue UI.
- [x] 5.10 Add synthetic queue simulator.
- [ ] Gate: scoring/order, starvation, concurrency, rebuild/recovery, and queue latency/realtime thresholds pass; queue domain coverage meets threshold.

Code and local tests for steps 5.1–5.10 are recorded in the [Phase 5 report](docs/phase-reports/phase-05.md). Its live-service concurrency, rebuild/recovery, and performance measurements still need configured PostgreSQL and Redis.

## Phase 6 — Red flags and department routing

- [x] 6.1 Implement multilingual text normalization and test Unicode/script variants.
- [x] 6.2 Define validated red-flag rules with required examples and draft review status.
- [x] 6.3 Implement deterministic rule matching, negation, conditions, and urgency ordering.
- [x] 6.4 Add English, Hindi, Marathi, and Hinglish starter rules.
- [x] 6.5 Implement explainable department routing and test cases.
- [x] 6.6 Integrate rule evaluation, event deduplication, queue reprioritization, and neutral patient messages.
- [x] 6.7 Add staff alert delivery, acknowledgement, escalation, sound toggle, and banner.
- [x] 6.8 Create labelled synthetic red-flag/routing evaluation datasets with fixed splits.
- [x] 6.9 Add evaluation commands/reports; tune on dev only and report test results.
- [x] 6.10 Replace routing stub and add staff override control.
- [x] Gate: test-split recall 100%, must-catch 60/60, precision 100%, negation 50/50, routing 96/96, staff alert p95 12.56 ms, red-flag/routing domain coverage 100%. See the [Phase 6 report](docs/phase-reports/phase-06.md); clinician and native-speaker sign-off remains pending.

## Phase 7 — Doctor/nurse consoles and PDF

- [x] 7.1 Finalize staff layout and role-based navigation; guarded admin, doctor, and nurse routes pass the 18-combination access matrix.
- [x] 7.2 Build live, virtualized staff queue board with filters and alert controls.
- [x] 7.3 Add audited, versioned case-sheet read/edit API with conflict detection.
- [x] 7.4 Build doctor consult view, edits, transcript access, and consult actions.
- [x] 7.5 Build nurse assisted registration, alerts, overrides, and no-show actions.
- [x] 7.6 Build admin user, kiosk, and department screens.
- [x] 7.7 Add audited multilingual PDF export and text-content tests.
- [ ] 7.8 Complete loading/empty/error states and print styles.
- [ ] 7.9 Add doctor/nurse/admin Playwright and access/conflict tests.
- [ ] Gate: complete role matrix, edit conflict, PDF latency/content, large queue/live update, audit, accessibility, and coverage thresholds.

## Phase 8 — Voice input

- [ ] 8.1 Re-check current official Sarvam STT docs and record provider ADR; use mock if no key is available.
- [ ] 8.2 Add STT interface, mock/Sarvam adapters, provider chain, circuit breaker, and metrics.
- [ ] 8.3 Add authenticated, rate-limited audio WebSocket with frame/time limits and no-audio-storage default.
- [ ] 8.4 Capture/resample browser microphone audio and handle permission/VAD states.
- [ ] 8.5 Integrate live transcript, editing, confidence check, and voice UI states.
- [ ] 8.6 Support mixed-language speech without changing the original script.
- [ ] 8.7 Fall back to touch/typing within three seconds on provider failure.
- [ ] 8.8 Add consent-based evaluation plan/audio (ignored and deleted after use) plus Mock CI evaluation.
- [ ] 8.9 Add protocol/provider/Playwright tests.
- [ ] Gate: fallback and protocol tests pass; report live WER/keyword/latency numbers if provider key is available; confirm no audio stored by default.

## Phase 9 — AI layer: smart questions, extraction, second opinion

- [ ] 9.1 Re-check official Gemini docs and record model/schema ADR.
- [ ] 9.2 Add LLM interface, mock/Gemini adapters, timeout/retry/circuit breaker/caps/metrics.
- [ ] 9.3 Implement PII redaction and tests for sensitive identifier formats.
- [ ] 9.4 Add versioned, safety-constrained prompts and prompt hash/version recording.
- [ ] 9.5 Add evidence-backed case extraction, validation, async behavior, and deterministic fallback.
- [ ] 9.6 Add bounded approved-question selection with a fast deterministic fallback.
- [ ] 9.7 Add red-flag second opinion that can only raise urgency; property-test this rule.
- [ ] 9.8 Add constrained department classification and nurse resolution on disagreement.
- [ ] 9.9 Add multilingual output guards against diagnosis/advice/medications/URLs.
- [ ] 9.10 Add generic-only cache, per-hospital enable flag, token limits, and metrics.
- [ ] 9.11 Create extraction/adversarial datasets and mock/live evaluation commands.
- [ ] 9.12 Show source/confidence on staff sheets; avoid AI-specific patient UI.
- [ ] Gate: meet extraction F1, hallucination, 0/100 safety leaks, red-flag/routing, latency/fallback, and PII thresholds.

## Phase 10 — Read-back, TTS, and patient confirmation

- [ ] 10.1 Verify current official TTS docs; record ADR and implement mock/Sarvam/Google adapters.
- [ ] 10.2 Generate fixed prompt audio and manifest; check all languages/prompts in CI.
- [ ] 10.3 Implement user-activated fixed audio playback and silent failure behavior.
- [ ] 10.4 Build ≤90-word deterministic patient-specific read-back; never cache patient-specific audio.
- [ ] 10.5 Add confirm/change flow, rerun safety/routing on edits, and nurse-help timeout.
- [ ] 10.6 Show confirmation time and corrections in the staff view.
- [ ] 10.7 Add multilingual golden, manifest, change/timeout/TTS/offline Playwright tests.
- [ ] Gate: all prompt audio covered, read-back ≤90 words, confirmation flows work, offline fixed audio makes zero network requests, and timestamps persist.

## Phase 11 — Notifications and analytics (optional if time is short)

- [ ] 11.1 Add notification interface with console default and disabled real providers; require patient opt-in.
- [ ] 11.2 Deliver outbox notifications exactly once with retries/dead-letter and per-phone limits.
- [ ] 11.3 Add periodic analytics rollups and summary/timeseries endpoints.
- [ ] 11.4 Build admin charts, visible time-saved assumption, and streamed CSV.
- [ ] 11.5 Test deduplication, opt-in, retries, analytics accuracy/performance, and accessibility.
- [ ] Gate: exactly-once/opt-in/dead-letter/analytics/performance/accessibility thresholds pass.

## Phase 12 — Offline PWA and document upload (upload is stretch)

- [ ] 12.1 Cache shell/fonts/locales/prompts and question bank; handle app updates.
- [ ] 12.2 Detect offline state using browser signal and server heartbeat.
- [ ] 12.3 Build short offline intake form and immediate neutral nurse message for danger tiles.
- [ ] 12.4 Store idempotent submissions locally, replay with backoff, and show provisional token.
- [ ] 12.5 Replay submissions through the server's real interview engine exactly once.
- [ ] 12.6 (Stretch) Add secure MinIO upload with size/type validation, EXIF removal, optional malware scan.
- [ ] 12.7 (Stretch) Extract document fields as unconfirmed, confidence-labelled suggestions.
- [ ] 12.8 Test offline/reconnect/replay/restart and, if built, upload/extraction safety.
- [ ] Gate: exactly one visit despite duplicate/interrupted replay, offline danger message <1 second, installable PWA; stretch gates apply only if upload is built.

## Phase 13 — Hardening, performance, and scale readiness

- [ ] 13.1 Run all security scans; fix high/critical results and document medium findings.
- [ ] 13.2 Run API fuzzing, JWT/IDOR, tenant-isolation, and WebSocket abuse tests.
- [ ] 13.3 Finalize security policy, pin dependencies, and generate SBOM.
- [ ] 13.4 Add PgBouncer, query review, monthly partitions, timeouts, and read URL support.
- [ ] 13.5 Split state Redis from cache Redis with separate policies.
- [ ] 13.6 Tune/configure workers, protocol, compression, DB pools; add N+1 and bundle checks.
- [ ] 13.7 Implement three documented k6 load scenarios and 30-minute soak.
- [ ] 13.8 Add failure-injection chaos scripts and assertions.
- [ ] 13.9 Add tracing, metrics, dashboards, and alert rules.
- [ ] 13.10 Run backup/restore drill and document recovery targets.
- [ ] 13.11 Add retention/anonymization/deletion, privacy/safety/scaling docs, and Kubernetes skeleton.
- [ ] Gate: zero high/critical findings, no fuzz 5xx, all security/load/soak/chaos/restore/N+1/retention thresholds pass; record machine details.

## Phase 14 — Demo polish and documentation

- [ ] 14.1 Add deterministic demo seed/stories and demo-safe provider mode.
- [ ] 14.2 Polish screens, empty/loading states, icons, logo slot, splash, native-speaker copy list; remove dev routes from production.
- [ ] 14.3 Add judge mode with latency/safety indicators and optional explainer.
- [ ] 14.4 Complete README, architecture, demo, evaluation, clinician review, safety, privacy, and scaling documents.
- [ ] 14.5 Capture screenshots under `docs/screenshots/`.
- [ ] 14.6 Run and summarize full regression and short load run.
- [ ] 14.7 Tag/build/smoke-test release artifacts as described by the playbook.
- [ ] Gate: full regression green, demo-safe mode works offline, human completes demo twice, docs complete, and all pending clinical review items are listed.

## Demo and delivery checklist

- [ ] Follow the exact three-minute script in Part J; fill in measured red-flag recall and latency.
- [ ] Use only synthetic/demo content; never connect to real patient records or live hospital/ABHA systems.
- [ ] If cutting scope, follow the playbook's cut order: document upload, real SMS/WhatsApp, reduce Phase 13 to core scans/load/chaos; never cut consent, red-flag evaluation, deterministic fallback, patient confirmation, audit logging, safety footer, or demo-safe mode.
- [ ] Complete the phase report and wait for explicit human approval before moving to the next phase.
