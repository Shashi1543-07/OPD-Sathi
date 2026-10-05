# OPD Saathi — Agent Build Playbook

**Version 1.0 · 5 October 2026**
**Project:** SIH26047 "Patient Case-Taking Software" — VNIT hackathon build
**Audience:** the coding agent that will build the product, and the human owner who tests each phase.

> **In plain words:** This file is the single source of truth for building OPD Saathi, a voice-first, multilingual history-taking and queue system for crowded government hospital OPDs. It tells the agent exactly what to build, in what order, with which tools, and how to prove each piece works before moving on. It tells you (the human) what to click and which numbers to expect at the end of every phase.

---

# Part A — Read me first

## A1. Mission in five lines

1. A patient at a crowded government hospital taps a language and answers a few questions by voice or touch.
2. The system writes a structured **case sheet** (history only) for the doctor.
3. It routes the patient to the right department and flags urgent cases for the nurse.
4. It gives the patient a token and a live queue position on the kiosk slip and on their phone.
5. It must be fast, safe, private, and keep working when the network or an AI service fails.

## A2. Phase index (the whole project on one page)

| # | Phase | Features | What the human can see and test | Key gate |
|---|---|---|---|---|
| 0 | Foundations and tooling | – | API docs page and empty web app run | CI green |
| 1 | UI foundation and language selection | F1 | Language tiles, themes, 3 languages | Accessibility and tap-target checks |
| 2 | Identity, audit, rate limiting | F11 (login), F15 (core) | Staff login, kiosk activation, a 429 error | Route-matrix security test |
| 3 | Data model, seeds, pagination | F5 (schema) | Seeded demo hospital, paginated lists | Encryption, tenant isolation, pagination tests |
| 4 | Deterministic interview and case sheet (no AI) | F2 (consent), F4 core, F5 | Consent, then a full touch-only interview producing a case sheet | 30 of 30 golden interviews |
| 5 | Queue, token, live position | F9 | Token slip, QR page, live position | Concurrency and ordering tests |
| 6 | Red flags and department routing | F7, F8 | Urgent alert appears on nurse screen | Recall at least 98%, must-catch 100% |
| 7 | Doctor and nurse consoles, PDF | F10, F11 | Doctor sees one-page case sheet | Role-access and PDF tests |
| 8 | Voice input (speech to text) | F3 | Speak in Hindi, Marathi, English | Word error rate and fallback tests |
| 9 | AI layer: smart questions and extraction | F4, F5, F7 | Smarter questions, auto-filled sheet | F1 at least 0.90, zero diagnosis leaks |
| 10 | Read-back, text to speech, confirmation | F6 | Patient hears and confirms summary | Cached prompts, end-to-end test |
| 11 | Notifications and analytics | F9, F13 | SMS or WhatsApp, admin charts | Exactly-once delivery, query speed |
| 12 | Offline PWA and document upload | F14, F12 | Works offline, prescription upload | Exactly-once sync |
| 13 | Hardening, performance, scale readiness | F15 | Load, chaos and security scan reports | Load thresholds, zero high findings |
| 14 | Demo polish and documentation | – | Demo script, docs, screenshots | Full regression |

**Priority if time is short:** phases 0 to 10 and 14 are the winning demo. Phase 13 is strongly recommended. Phases 11 and 12 are optional (see the cut list in Part J).

## A2b. The feature list used above

F1 language selection and kiosk UI · F2 consent and identity · F3 multilingual speech to text · F4 adaptive interview · F5 structured case sheet · F6 patient confirmation loop · F7 red-flag and urgency detection · F8 department routing · F9 token and live queue · F10 doctor dashboard · F11 nurse and staff console · F12 old prescription upload · F13 analytics · F14 offline mode · F15 privacy and security.

## A3. Fourteen operating rules (non-negotiable)

1. **One phase at a time.** Do not start phase N+1 until the human writes "approve phase N".
2. **Brick by brick.** Inside a phase, do the numbered build steps in order. Run that step's test before the next step. Commit after every green step.
3. **No step without a test.** Every step has an automated test (unit, integration, Playwright, or an evaluation script) unless the step is marked "manual only".
4. **Numbers, not adjectives.** Every gate is a number. Print the measured value next to the threshold. Never write "passed" without the printed output.
5. **Never weaken a test.** Do not lower a threshold, skip, xfail, or delete a test to get green. If a gate cannot be met, stop and report what failed, why, and the options.
6. **History-taking, not diagnosis.** The product and every AI prompt must never output: a diagnosis or likely disease, a medicine or dose, advice like "you should" or "you do not need", or reassurance like "it is nothing serious". See B6.
7. **Humans own medical content.** Question banks, red-flag rules and routing rules written by you are drafts. Mark them `status: draft_needs_clinician_review` until a clinician signs off. The UI footer says the tool collects history and does not diagnose.
8. **Synthetic data only.** Never use or invent real patient data. Use Faker and scripted dialogues. Voice clips for evaluation come from consenting teammates and are deleted afterwards.
9. **Stay inside the locked stack (Part C).** No new framework, database or major library without an ADR entry in `docs/DECISIONS.md` and the human's approval.
10. **Adapters for everything external.** Speech to text, LLM, text to speech, SMS and WhatsApp each sit behind an interface with a `Mock` implementation (used in tests and in demo-safe mode) and a defined fallback.
11. **Safety-biased.** When urgency is uncertain, escalate. The LLM may raise urgency but must never lower it.
12. **Secure by default.** Deny-by-default routes, validated input, least privilege, no secrets in code or logs, no personal data in logs.
13. **Explainable.** Every phase ends with a plain-language explanation for a non-expert. Non-obvious code gets a short docstring. The README is updated in the same commit.
14. **Fast by budget.** Respect the performance budgets in G8. Measure with real tools. Do not micro-optimise early, but never ship over budget.

## A4. The phase loop

```
READ phase section  ->  PLAN (list steps, files, tests)  ->  BUILD step 1  ->  TEST step 1
      ->  commit  ->  BUILD step 2 ...  ->  MEASURE gate numbers  ->  WRITE Phase Report
      ->  STOP and wait for "approve phase N"
```

At the end of every phase, write `docs/phase-reports/phase-NN.md` using the template in Appendix C and also print it in chat. It must contain: what was built (plain words), commands run, test counts, measured gate numbers against thresholds, screenshots (paths), known issues, how the human tests it manually, and a preview of the next phase. End the message with: "Reply `approve phase N` to continue."

## A5. Stop and ask the human when

- A gate fails after two fix attempts.
- A provider's real API differs from what this file says.
- A clinical-content choice is needed (a rule, a question, a threshold with patient-safety impact).
- An action is destructive (dropping a database, rewriting git history).
- A paid account, API key or hardware is needed.
- Two instructions in this file conflict.

Ask **one** clear question with a recommended default. Do not stop for anything else.

## A6. Decision defaults (use these instead of deliberating)

| Topic | Default |
|---|---|
| Python | 3.12, type hints everywhere, Ruff (line length 100), mypy strict on `app/domain` and `app/core` |
| TypeScript | strict mode, no `any` without a comment, ESLint + Prettier |
| Naming | Python `snake_case`; TypeScript `camelCase`; React components `PascalCase`; DB tables plural `snake_case`; API paths kebab-case |
| IDs | UUIDv7 (library `uuid-utils`) for all primary keys |
| Time | Store UTC; display in `Asia/Kolkata`; ISO 8601 in APIs |
| Languages | `en`, `hi`, `mr` in the MVP. Locale mapping for speech: `en-IN`, `hi-IN`, `mr-IN` |
| Errors | RFC 7807 `application/problem+json` with a stable `code` and `request_id` |
| Git | One branch per phase (`phase-NN-name`), conventional commits, tag `phase-NN-passed` after approval |
| Docs | Update README and the phase report in the same commit as the code |
| Unknown library version | Install latest stable, pin in the lockfile, record in `docs/DECISIONS.md` |
| Operating system | Assume Linux, macOS or WSL2 with Docker. Ask the human once in Phase 0 which they use |

## A7. Prompts the human can paste to the agent

- **Start a phase:** "Read AGENT_PLAYBOOK.md Parts A to G once. Then execute Phase N exactly as written. Stop at the gate and give me the Phase Report."
- **A gate failed:** "Gate X failed in Phase N. Do not change thresholds. Diagnose with evidence, propose a fix, apply it, re-run the whole phase test suite, and update the report."
- **Explain it:** "Explain what you built in Phase N in plain language, with a small diagram, as if to a judge who is not technical."
- **Verify it:** "Run the Definition-of-Done checklist for Phase N and show evidence for each line."
- **Regression:** "Run the full regression (`make test`, `make e2e`, `make eval`) and show a summary table of results against the gates."

---

# Part B — The product on one page

## B1. The problem (SIH26047, "Patient Case-Taking Software")

Government hospital OPDs are crowded. One doctor may see 80 to 200 or more patients in a four-hour morning, leaving about two to three minutes each. Patients describe problems in unstructured ways and in many languages, so the doctor loses time and context. Queues have no synchronised token system, so crowds build and nobody knows the wait. Old prescriptions and reports are on paper and not available in the room.

> **Check before submission:** the problem text above is paraphrased from other teams' public repositories. Confirm the exact wording on the official SIH portal and paste it into `docs/PROBLEM_STATEMENT.md`.

## B2. The solution in one sentence

A multilingual, voice-first history-taking assistant that turns what a patient says in the waiting area into a structured case sheet for the doctor, routes the patient to the right department, flags urgent cases, and shows live queue position.

## B3. Users and roles

| Role | Who | Main screen | Can do |
|---|---|---|---|
| Patient | Person waiting for OPD | Kiosk, later own phone | Answer questions, confirm summary, see queue position |
| Nurse / reception | Desk staff | Nurse console | Assist registration, see urgent alerts, override department or urgency, call next |
| Doctor | OPD doctor | Doctor dashboard | See queue, read and edit case sheet, start and finish consults |
| Admin | Hospital IT | Admin and analytics | Manage users, kiosks, departments; view analytics and audit log |
| Kiosk | Tablet in the waiting area | Kiosk app (device identity) | Create intake sessions only |

## B4. The end-to-end journey

1. Patient taps a language and hears and reads a short consent.
2. Patient taps any **danger signs** (chest pain, trouble breathing, heavy bleeding, and so on).
3. Patient gives age, sex and optionally a phone number by touch.
4. Patient answers the interview by voice (or touch). Questions adapt to the complaint.
5. Patient hears and sees a summary in their own language and confirms or corrects it.
6. System assigns department, urgency and token, and prints or shows a QR code.
7. Patient watches live position on the kiosk slip or phone. Nurse is alerted instantly if urgent. Doctor opens a one-page case sheet when the patient is called.

## B5. Scope

**In scope:** F1 to F15 as listed in A2b, in English, Hindi and Marathi, with a path to more languages.

**Out of scope (do not build):** diagnosis, medicine suggestions, billing, live integration with a real hospital information system or real ABHA servers (mock only), video consults, real patient data.

## B6. Safety framing (applies to UI text, prompts and code)

- The product **collects history**. It never diagnoses, never names a likely disease, never suggests or comments on medicines, never reassures, never tells the patient what to do beyond "tell the nurse" or "please wait".
- Every AI-filled field is editable. The patient confirms the summary before it reaches the doctor. The doctor can edit everything.
- Urgency is shown to staff as "needs early attention" with the reason. Patients see only: "Please inform the staff at the desk now." Do not show the word "urgent" or alarming text to patients.
- Self-harm statements alert staff silently. The patient sees the same neutral message.
- A footer on staff screens and the PDF reads: "Collected by OPD Saathi from the patient's own statements. History only. Not a diagnosis."
- A post-processing filter rejects any AI output that looks like a diagnosis or a recommendation (see Phase 9).

## B7. Success metrics

| Metric | Target |
|---|---|
| Interview time per patient (touch and voice) | 3 minutes or less typical, 5 minutes hard cap |
| Doctor can read the case sheet | Under 20 seconds, one page |
| Urgent cases caught (rules + LLM) on test set | At least 99% recall on the must-catch subset: 100% |
| Voice turn latency (speech end to spoken reply) | 1.5 seconds or less at p95 |
| Queue position update to the phone | 200 ms or less at p95 |
| Works with network or AI service down | Yes, via touch, typed and offline fallbacks |

---

# Part C — Locked decisions

## C1. The stack (do not change without an ADR and human approval)

| Layer | Choice | Why this one |
|---|---|---|
| Web app | React 18+, Vite, TypeScript (strict) | One codebase for kiosk, staff and patient pages; fast builds |
| Styling and components | Tailwind CSS, shadcn/ui (Radix primitives), lucide-react icons | Accessible parts, consistent design tokens |
| Web state and data | TanStack Query (server state), Zustand (small client state), React Router, react-hook-form + zod | Predictable caching and forms |
| i18n | i18next + react-i18next, JSON per language | Hindi, Marathi, English now; more later |
| Charts and QR | Recharts, qrcode.react | Simple and light |
| PWA and offline | vite-plugin-pwa (Workbox), `idb` for IndexedDB | Offline kiosk shell and queued submissions |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + asyncpg, Alembic | Async, typed, matches the AI libraries |
| Database | PostgreSQL 16+ | Source of truth, row-level security, partitioning |
| Cache, queue read-model, limits, pub/sub | Redis 7+ (redis-py asyncio) | Sub-millisecond reads, atomic Lua scripts |
| Background jobs | arq (Redis based) | Notifications, analytics rollups, OCR |
| Auth | PyJWT, argon2-cffi | Short-lived tokens, strong password hashing |
| Crypto | `cryptography` (AES-256-GCM) | Field-level encryption |
| PDF | WeasyPrint | HTML to PDF with Devanagari support |
| Speech to text | Sarvam AI (primary), Google Cloud Speech-to-Text (optional fallback), Mock | Best Indian-language coverage |
| LLM | Google Gemini Flash-class model via `google-genai` (primary), Mock | Fast, cheap, JSON-schema output |
| Text to speech | Sarvam TTS (primary), Google Cloud TTS (fallback), Mock | Indic voices, prerendered prompts |
| Logging and tracing | structlog (JSON), OpenTelemetry, Prometheus, Grafana, Sentry (optional) | Standard and portable |
| Testing | pytest, pytest-asyncio, Hypothesis, httpx, Vitest, Testing Library, Playwright, `@axe-core/playwright`, k6, schemathesis, jiwer | Covers unit to load |
| Security scanning | Bandit, pip-audit, npm audit, Semgrep, gitleaks, OWASP ZAP baseline | Cheap and automatic |
| Local infra | Docker Compose (postgres, redis, api, worker; minio from Phase 12) | One command to run everything |

## C2. Build-now versus production target

The architecture from the planning stage stays the **target**. To keep the build reliable for an agent and a small team, some boxes are implemented simpler now behind the same boundaries.

| Target component (production) | Built in this project (MVP) | Upgrade trigger |
|---|---|---|
| Cloudflare CDN, WAF, DDoS | Caddy reverse proxy in Compose, security headers in the app | Real deployment |
| Kong or Envoy API gateway | FastAPI middleware (auth, rate limit, validation) | More than one backend service |
| Keycloak (OIDC) | In-app JWT auth with OIDC-shaped claims (`sub`, `role`, `hospital_id`) | Hospital SSO needed |
| Go voice gateway and Go queue service | Python modules `api/v1/ws_audio.py` and `domain/queue` | Load test shows the Python path misses a budget |
| NATS JetStream event bus | Postgres **outbox** table + arq workers + Redis pub/sub for realtime | Multi-service fan-out |
| Istio or Linkerd mTLS | Private Docker network, no public ports except the proxy | Kubernetes deployment |
| Vault and KMS | Environment variables with versioned keys (`v1:` prefix) so rotation is possible | Real deployment |
| Kubernetes multi-AZ | Docker Compose, plus manifests skeleton in `infra/k8s` | Real deployment |
| Postgres HA and read replicas | Single Postgres; optional `DATABASE_READ_URL` supported in code | Dashboards slow down |
| Self-hosted fallback speech model | Adapter stub `LocalWhisperSTT` (not implemented) | Data must not leave hospital |

Keep the **module boundaries** exactly as the architecture shows (voice, AI, queue, case, auth, notifications) so splitting a module into its own service later is a mechanical change.

## C3. External provider facts (checked on 5 October 2026 — re-check before coding each adapter)

Provider details change often. Before writing each adapter, fetch the current official docs, then record what you found in `docs/DECISIONS.md` (ADR "Provider: X").

| Provider | What was found | Official docs |
|---|---|---|
| Sarvam speech to text (streaming) | WebSocket at `wss://api.sarvam.ai/speech-to-text/ws`; API key sent in the `Api-Subscription-Key` header; default model listed as `saaras:v3`; the FAQ at check time said streaming supports **Hindi and Marathi only** | `https://docs.sarvam.ai` (index: `/llms.txt`; an MCP server for coding agents is at `https://docs.sarvam.ai/_mcp/server`) |
| Sarvam speech to text (REST) | `https://api.sarvam.ai/speech-to-text` for clips up to 30 seconds; a batch API for longer audio; invalid keys return HTTP 403, not 401 | same |
| Gemini LLM | Docs listed `gemini-3.8-flash` and `gemini-3.5-flash-lite` as current Flash-class stable models, with structured (JSON) output supported | `https://ai.google.dev/gemini-api/docs/models` |
| Sarvam text to speech | Not verified in this check — read the docs index first | `https://docs.sarvam.ai` |

**Consequences you must implement:**

- English and any language without streaming support use the **REST path on short clips** (client-side voice-activity detection ends the clip, maximum 30 seconds, ideally under 15).
- Model names live in environment variables (`LLM_MODEL`, `LLM_FALLBACK_MODEL`, `STT_MODEL`, `TTS_MODEL`). Never hardcode them in business logic.
- If a doc contradicts this table, trust the doc, write the ADR, and tell the human.

## C4. Repository layout (create exactly this)

```
opd-saathi/
├─ README.md
├─ AGENT_PLAYBOOK.md                 (this file)
├─ Makefile
├─ docker-compose.yml
├─ .env.example
├─ .github/workflows/ci.yml
├─ docs/
│  ├─ DECISIONS.md                   (ADR log)
│  ├─ PROBLEM_STATEMENT.md
│  ├─ SAFETY.md  PRIVACY.md  EVALUATION.md  SCALING.md  DEMO.md
│  ├─ case_sheet.schema.json
│  └─ phase-reports/                 (phase-00.md ... phase-14.md)
├─ apps/web/
│  ├─ src/
│  │  ├─ app/                        (router, providers, error boundary)
│  │  ├─ features/{kiosk,staff,status,admin}/
│  │  ├─ components/ui/              (Saathi UI components)
│  │  ├─ lib/                        (api client, audio, offline, i18n, ws)
│  │  ├─ locales/{en,hi,mr}/*.json
│  │  └─ styles/tokens.css
│  ├─ public/audio/{en,hi,mr}/       (prerendered prompts + manifest.json)
│  └─ e2e/                           (Playwright)
├─ services/api/
│  ├─ app/
│  │  ├─ main.py  config.py  deps.py  cli.py
│  │  ├─ core/    (security, crypto, ratelimit, idempotency, pagination, errors, logging, audit)
│  │  ├─ db/      (models, session, rls, migrations/)
│  │  ├─ domain/
│  │  │  ├─ case_sheet.py
│  │  │  ├─ interview/   (engine.py, bank/*.json)
│  │  │  ├─ redflags/    (engine.py, normalize.py, rules.yaml)
│  │  │  ├─ routing/     (engine.py, rules.yaml)
│  │  │  └─ queue/       (scoring.py, service.py, eta.py, lua/*.lua)
│  │  ├─ ai/
│  │  │  ├─ providers/{stt,llm,tts}/   (base.py, sarvam.py, gemini.py, mock.py ...)
│  │  │  ├─ prompts/v1/*.md
│  │  │  ├─ redact.py  orchestrator.py  guards.py
│  │  ├─ api/v1/          (auth, intake, queue, status, cases, admin, analytics, ws_audio, ws_staff)
│  │  └─ workers/         (arq tasks: notifications, analytics, ocr, outbox_relay)
│  └─ tests/{unit,integration,security,perf}/
├─ eval/                             (datasets and scripts; all synthetic)
│  ├─ redflags/  routing/  interview/  stt/  extraction/  prescriptions/
└─ infra/                            (caddy/, k6/, grafana/, prometheus/, k8s/, zap/)
```

## C5. Local services and ports

| Service | Port | Notes |
|---|---|---|
| Web dev server | 5173 | Vite |
| API | 8000 | FastAPI docs at `/docs` (disabled when `APP_ENV=production`) |
| PostgreSQL | 5432 | App connects as role `osa_app` (no superuser, no RLS bypass) |
| Redis | 6379 | Password required even in dev |
| MinIO (Phase 12) | 9000 / 9001 | S3-compatible object store |

---

# Part D — Architecture and flows

## D1. Layers and trust zones

```
 PUBLIC ZONE (untrusted)
 +--------------+   +---------------+   +----------------+
 |  Kiosk PWA   |   |  Staff web    |   | Patient phone  |
 +------+-------+   +-------+-------+   +--------+-------+
        |                   |                    |
        +-------------------+--------------------+
                            | HTTPS and WSS, TLS 1.3
                 +----------v-----------+
                 | CDN + WAF (prod)     |
                 | Caddy proxy (dev)    |
                 +----------+-----------+
 ---------------------------|--------------------------------
 PRIVATE ZONE               |
                 +----------v-----------+
                 |      FastAPI API     |
                 | request-id, JWT auth |
                 | rate limit, RLS,     |
                 | idempotency, audit   |
                 +--+-----+------+----+-+
                    |     |      |    |
            +-------v-+ +-v----+ +v---v-----+  +-------------------+
            |Postgres | |Redis | | arq worker|  | AI adapters       |
            |(truth)  | |cache | | outbox,   |  | STT, LLM, TTS     |
            +---------+ |queue | | notify,   |  | via egress proxy  |
                        |limits| | analytics |  | + PII redaction   |
                        +------+ +-----------+  +-------------------+
```

**Rules of the zones**

- Only the proxy is exposed. Postgres, Redis and workers have no public ports.
- The AI adapters are the **only** code allowed to call the internet. They sit behind one egress function that redacts personal data and enforces timeouts and circuit breakers.
- Every tenant table carries `hospital_id`, and PostgreSQL row-level security enforces it (Part E1).

## D2. The voice turn (the hot path)

```
 Mic -> AudioWorklet (16 kHz PCM, 100 ms frames) -> WebSocket -> STT adapter
   -> partial text shown live -> final text -> interview engine picks next question
   -> (LLM may choose among candidates, with a time limit) -> prerecorded prompt audio plays
 In parallel, never blocking the reply:
   red-flag check | case-sheet merge | queue re-rank
```

| Step | Budget (design target, to be measured) |
|---|---|
| Audio chunk upload (streamed) | under 100 ms |
| Final transcript after speech ends | under 300 ms |
| Next question (bank lookup, optional small LLM choice) | under 600 ms |
| Spoken reply (prerendered audio, or streamed TTS) | under 200 ms |
| **Whole voice turn** | **about 1.2 s, hard limit 1.5 s at p95** |

If any step exceeds its time limit, take the **deterministic path**: bank-selected question, prerecorded audio, rules-only red flags. The patient must never see a spinner longer than 2 seconds.

## D3. Queue design (source of truth and read model)

- **PostgreSQL is the source of truth.** The `visits` row holds status, urgency, department and token. A transactional **outbox** row records every queue change.
- **Redis is the read model.** One sorted set per hospital and department: `osa:{env}:{hospital}:q:{dept}`. Member is `visit_id`. Score is the *virtual arrival time* (formula in E6). Lower score is served first. Position is `ZRANK + 1`.
- **Write path:** (1) one database transaction creates or changes the visit and the outbox row; (2) after commit, the API runs an atomic Lua script that updates the sorted set and publishes a delta; (3) if step 2 fails, the `outbox_relay` worker retries every 2 seconds. Redis operations are idempotent, so retries are safe.
- **Repair:** on API start, and via `python -m app.cli queue-rebuild`, rebuild every sorted set from Postgres. Tests must prove "flush Redis, rebuild, same order".

## D4. Realtime protocols

**Staff WebSocket** `GET /api/v1/ws/staff/queue?department_id=...` (JWT in the first message, not in the URL)

Server to client:
```json
{"type":"queue.snapshot","seq":120,"items":[{"visit_id":"...","token":"MED-017","urgency":"urgent","position":1}]}
{"type":"queue.delta","seq":121,"ops":[{"op":"upsert","item":{}},{"op":"remove","visit_id":"..."}]}
{"type":"alert.urgent","seq":122,"visit_id":"...","token":"MED-018","reasons":["Chest pain with sweating"]}
{"type":"ping"}
```
Client to server: `{"type":"auth","token":"..."}`, `{"type":"ack_alert","visit_id":"..."}`, `{"type":"pong"}`.
`seq` increases by one per message. If the client sees a gap, it refetches the snapshot. Heartbeat every 25 seconds.

**Patient status stream** `GET /api/v1/status/{status_token}/stream` (Server-Sent Events, auto-reconnect)
```
event: status
data: {"state":"waiting","token":"MED-017","position":4,"eta_min":12,"eta_max":20}
```

**Audio WebSocket** `GET /api/v1/ws/intake/{visit_id}/audio`
- Client to server binary: PCM16 mono 16 kHz, **3,200 bytes per frame** (100 ms). Reject frames larger than 8,192 bytes.
- Client to server text: `{"type":"start","lang":"hi","question_id":"Q_CC"}`, `{"type":"end"}`.
- Server to client text: `{"type":"partial","text":"..."}`, `{"type":"final","text":"...","confidence":0.0}`, `{"type":"error","code":"STT_UNAVAILABLE"}`, `{"type":"fallback","mode":"touch"}`.
- Limits: 60 seconds per utterance, 4 concurrent sockets per kiosk, idle timeout 15 seconds.

## D5. Failure modes and required behaviour

| What fails | What the system must do |
|---|---|
| LLM slow or down | Use the deterministic interview engine and rules-only red flags. No error shown to the patient |
| Speech to text down | Show "Please type or tap your answers" within 3 seconds; offer nurse-assisted entry |
| Text to speech down | Show text and play prerecorded prompts only; skip dynamic read-back audio |
| Redis down | Queue reads fall back to Postgres (slower, still correct). Login and OTP rate limits **fail closed** (return 503). Other limits fall back to an in-process limiter |
| Worker down | Notifications delay; outbox grows; alert if oldest unprocessed event is older than 60 seconds |
| Postgres down | `/readyz` returns 503; kiosk shows "Please go to the nurse desk"; offline PWA keeps local progress (Phase 12) |
| Network down at kiosk | Offline PWA completes the interview and queues the submission with an idempotency key |
| Provider returns garbage | JSON-schema validation fails, output is discarded, deterministic path is used, event is logged |

## D6. How the pieces fit — a plain-words picture

Think of a hospital reception desk. The **kiosk** is a very patient clerk who asks the same good questions in the patient's language. The **speech service** is the clerk's ears. The **interview engine** is the clerk's checklist, so the questions stay safe and in order. The **AI** is a helper who writes tidy notes from what was said, and can only choose from approved questions. The **red-flag rules** are a bell that rings for the nurse if certain danger words come up. The **queue** is the token machine that always knows who is next. **Postgres** is the permanent register book. **Redis** is the whiteboard everyone can read instantly. The **doctor dashboard** is the one page the doctor reads before saying "next".

---

# Part E — Data, domain rules and API contract

## E1. Data model (PostgreSQL)

All tables have `id` (UUIDv7), `created_at`, and (except `hospitals`) `hospital_id`. All times are UTC.

| Table | Key columns | Notes |
|---|---|---|
| `hospitals` | name, code, timezone (default `Asia/Kolkata`) | Tenant root |
| `departments` | hospital_id, code, token_prefix, name_en, name_hi, name_mr, is_active | Seed 8 (E5) |
| `users` | role (`admin`, `doctor`, `nurse`), name, email, password_hash, department_id, failed_logins, locked_until, is_active | Unique (hospital_id, email) |
| `kiosks` | label, secret_hash, is_active, last_seen_at | Device identity |
| `refresh_tokens` | user_id, family_id, token_hash, expires_at, revoked_at, replaced_by | Rotation with reuse detection |
| `patients` | name_enc, phone_enc, phone_hmac, abha_enc, year_of_birth, sex (`female`, `male`, `other`, `unknown`), preferred_language | Encrypted fields (below) |
| `consents` | visit_id, language, text_version, text_sha256, granted, granted_at, channel (`kiosk`, `nurse`) | Proof of consent |
| `visits` | patient_id, department_id, status, urgency (`routine`, `priority`, `urgent`), urgency_reasons (jsonb), token_number, token_label, service_date, status_token_hash, language, age_years, pregnant, registered_at, queued_at, called_at, consult_started_at, consult_ended_at, assigned_doctor_id | Unique (hospital_id, department_id, service_date, token_number) |
| `case_sheets` | visit_id (unique), schema_version, data (jsonb), version (int), confirmed_by_patient, confirmed_at, last_edited_by | `version` enables optimistic concurrency |
| `interview_turns` | visit_id, turn_index, question_id, input_mode (`voice`, `touch`, `text`, `nurse`), text_original, text_english, stt_provider, stt_confidence | Unique (visit_id, turn_index) |
| `red_flag_events` | visit_id, source (`tile`, `rule`, `llm`, `nurse`), rule_id, level, evidence, acknowledged_by, acknowledged_at | Drives alerts |
| `outbox` | topic, payload (jsonb), processed_at, attempts | Transactional events |
| `notifications` | visit_id, channel, template, status, provider_message_id | Phase 11 |
| `documents` | visit_id, object_key, mime, size, ocr_status, extracted (jsonb) | Phase 12 |
| `audit_log` | actor_type (`user`, `kiosk`, `patient`, `system`), actor_id, action, resource_type, resource_id, ip, user_agent, request_id, meta (jsonb) | **Append-only**: `REVOKE UPDATE, DELETE` from the app role |

**Visit status values:** `registering`, `awaiting_confirmation`, `queued`, `called`, `in_consult`, `done`, `cancelled`, `no_show`.

**Required indexes:** `visits (hospital_id, department_id, status, queued_at)`, `visits (hospital_id, service_date)`, `interview_turns (visit_id, turn_index)`, `audit_log (hospital_id, created_at DESC)`.

**Field encryption.** `name_enc`, `phone_enc`, `abha_enc` store `v1:` + base64(nonce ‖ ciphertext ‖ tag), AES-256-GCM, random 96-bit nonce, associated data `table:column:row_id`. Key from `APP_DATA_KEY` (32 bytes, base64). The `v1:` prefix allows key rotation. `phone_hmac` is HMAC-SHA256 with `APP_HMAC_KEY`, used for lookup. Plain values must never appear in the database, logs, traces or error messages.

**Row-level security.** Every tenant table: `ENABLE` and `FORCE ROW LEVEL SECURITY`, with policy `USING (hospital_id = current_setting('app.hospital_id', true)::uuid)`. The API runs `SET LOCAL app.hospital_id = ...` at the start of each request transaction. The app connects as role `osa_app`, which is neither owner nor superuser, so it cannot bypass the policy.

## E2. Case sheet (JSON, `schema_version` "1.0")

Define as Pydantic models in `app/domain/case_sheet.py` and export JSON Schema to `docs/case_sheet.schema.json`. Unknown fields are rejected.

```json
{
  "schema_version": "1.0",
  "language": "hi",
  "chief_complaint": {"text_original": "पेट में दर्द और बुखार", "text_en": "stomach pain and fever", "confidence": 0.93},
  "hpi": {
    "onset": null,
    "duration": {"value": 3, "unit": "day"},
    "location": "abdomen",
    "character": null,
    "severity_0_10": 6,
    "aggravating": [],
    "relieving": [],
    "radiation": null,
    "timing": null,
    "associated_symptoms": [{"text_en": "fever", "confidence": 0.95}],
    "pertinent_negatives": []
  },
  "past_history": {"conditions": ["diabetes"], "surgeries": []},
  "medications": [{"name": "metformin", "dose": null, "source": "patient_stated"}],
  "allergies": [],
  "family_history": [],
  "social": {"tobacco": null, "alcohol": null},
  "women_health": {"pregnant": null, "lmp": null},
  "red_flags": [{"id": "RF_HIGH_FEVER", "level": "priority", "source": "rule", "evidence": "fever 104"}],
  "routing": {"department_code": "GENMED", "confidence": 0.9, "reason_en": "Fever with abdominal pain", "needs_nurse_confirmation": false},
  "field_meta": {"/hpi/severity_0_10": {"source": "patient_touch", "confidence": 1.0, "confirmed": true}},
  "disclaimer": "Collected by OPD Saathi from the patient's own statements. History only. Not a diagnosis."
}
```

Rules: every free-text field has an original-language value and an English value. `field_meta` is keyed by JSON Pointer; `source` is one of `patient_voice`, `patient_touch`, `patient_text`, `nurse`, `doctor`, `document`. Confidence below **0.70** shows as a yellow chip for the doctor. `women_health` is only filled for females aged 12 to 50. Maximum sizes: any string 500 characters, any list 30 items.

## E3. Question bank (deterministic interview)

Files in `app/domain/interview/bank/`: `core.json`, `modules/*.json`, `triggers.json`. A validator (run in CI) fails the build if: ids are duplicated, a `next` points nowhere, any text lacks `en`, `hi` or `mr`, a module has more than 4 questions, or `reviewed_by` is missing (use `null` for drafts).

```json
{
  "id": "Q_DURATION",
  "stage": "core",
  "type": "duration",
  "text": {"en": "For how long have you had this problem?", "hi": "यह समस्या कितने समय से है?", "mr": "ही समस्या किती वेळापासून आहे?"},
  "fills": ["/hpi/duration"],
  "skippable": true,
  "reviewed_by": null
}
```

**Input types:** `free_voice` (voice or typed text), `yes_no`, `single_choice`, `multi_choice`, `number`, `duration` (number plus unit), `scale_0_10` (faces), `chips`. Option `value` codes are stable English slugs; labels are translated.

**Core questions (always, in this order):**

| Id | Input | Fills |
|---|---|---|
| `Q_CC` | free_voice | chief complaint |
| `Q_DURATION` | duration | `/hpi/duration` |
| `Q_SEVERITY` | scale_0_10 | `/hpi/severity_0_10` |
| *module questions (0 to 4)* | varies | HPI details, associated symptoms |
| `Q_CONDITIONS` | chips (diabetes, blood pressure, heart, asthma, TB, thyroid, kidney, none) | `/past_history/conditions` |
| `Q_MEDS` | free_voice with "none" | `/medications` |
| `Q_ALLERGY` | yes_no then free_voice | `/allergies` |
| `Q_PREGNANT` | yes_no_unsure, only females 12 to 50 | `/women_health/pregnant` |

Maximum **12** interview questions per patient (`MAX_QUESTIONS`). "I don't know" and "Skip" are always available. The engine never repeats a question.

**Module triggers** (keyword match on the chief complaint, case-insensitive, normalised; the match must work for English, Hindi, Marathi and common Hinglish spellings):

| Module | Example trigger words |
|---|---|
| `fever` | fever, temperature, bukhar, बुखार, ताप |
| `cough_breath` | cough, cold, breathless, khansi, खांसी, खोकला, सर्दी |
| `abdominal` | stomach pain, abdomen, pet dard, पेट दर्द, पोटदुखी |
| `chest` | chest pain, seene mein dard, सीने में दर्द, छातीत दुखणे |
| `headache` | headache, sir dard, सिर दर्द, डोकेदुखी |
| `msk_injury` | joint pain, back pain, fracture, chot, कमर दर्द, जोड़ों का दर्द, पाठदुखी, जखम |
| `skin` | rash, itching, khujli, खुजली, पुरळ |
| `eye` | eye, vision, aankh, आंख, डोळा |
| `ent` | ear, throat, nose, kaan, gala, कान, गला, घसा |
| `gi_vomit_diarrhoea` | vomiting, loose motion, ulti, dast, उल्टी, दस्त, जुलाब |
| `urinary` | urine, burning urine, peshab, पेशाब, लघवी |
| `gyne_pregnancy` | pregnant, periods, garbhwati, गर्भवती, मासिक पाळी, माहवारी |
| `surgical` | lump, swelling, hernia, piles, abscess, गांठ, गाठ |
| `general` (fallback) | no match |

If several modules match, run the one with the earliest keyword position first, then the next, up to the 12 question cap.

> **Agent rule:** write English first, then Hindi and Marathi drafts. Add each file to `docs/CLINICIAN_REVIEW.md` as "pending review" and tell the human that a clinician and a native speaker must review before the demo.

## E4. Danger-sign tiles and red-flag rules

**Danger-sign tiles** (kiosk screen after consent, large icon plus text plus voice prompt). Any tapped tile means **urgent** immediately. No AI involved.

| Tile id | Label (en) |
|---|---|
| `DS_CHEST` | Chest pain or pressure |
| `DS_BREATH` | Trouble breathing |
| `DS_BLEED` | Heavy bleeding |
| `DS_FIT` | Fits or fainting |
| `DS_INJURY` | Serious injury or accident |
| `DS_PREG` | Pregnancy trouble |
| `DS_STROKE` | Sudden weakness, face drooping or slurred speech |
| `DS_POISON` | Poison, overdose or snake or insect bite |
| *None of these* | large button at the bottom |

**Rule engine** (`app/domain/redflags/`):

1. **Normalise** text: Unicode NFKC, lowercase, remove punctuation, collapse spaces, normalise Devanagari (nukta variants, chandrabindu and anusvara), keep Latin transliterations (Hinglish) as separate phrases.
2. **Match** phrase groups from `rules.yaml` (any-of and all-of groups per language) plus structured conditions (age, sex, pregnancy, severity, tiles).
3. **Negation:** if a negation word appears within three tokens before or after a match ("no", "not", "nahi", "nahin", "नहीं", "नाही", "without"), downgrade **only if** the structured answers do not contradict it. When unsure, keep the flag. False positives are acceptable; misses are not.
4. **Output:** a list of flags, each with `id`, `level`, `source`, `evidence` (short quote of what the patient said) and a staff-facing `reason_en` such as "Chest pain with sweating reported". The visit's urgency is the **maximum** level.

```yaml
- id: RF_CHEST_PAIN_ASSOC
  level: urgent
  status: draft_needs_clinician_review
  reviewed_by: null
  reason_en: "Chest pain with sweating, breathlessness or arm/jaw pain reported"
  all_groups:
    - any: {en: ["chest pain", "chest pressure", "chest tightness"], hi: ["सीने में दर्द", "छाती में दर्द", "seene mein dard", "chhati me dard"], mr: ["छातीत दुखणे", "छातीत दुखत"]}
    - any: {en: ["sweat", "breathless", "left arm", "jaw", "nausea", "faint"], hi: ["पसीना", "सांस", "बाएं हाथ", "जबड़ा", "चक्कर"], mr: ["घाम", "दम", "डावा हात", "जबडा", "चक्कर"]}
```

**Starter rule set** (all drafts for clinician review):

| Rule id | Level | Fires when |
|---|---|---|
| `RF_DS_TILE` | urgent | any danger tile tapped |
| `RF_CHEST_PAIN_ASSOC` | urgent | chest pain/pressure plus sweating, breathlessness, arm or jaw pain, nausea, fainting |
| `RF_CHEST_PAIN` | priority | chest pain alone |
| `RF_BREATH_SEVERE` | urgent | severe breathlessness, cannot speak full sentences, blue lips |
| `RF_STROKE_SIGNS` | urgent | sudden one-sided weakness, face drooping, slurred speech, sudden vision loss, sudden very severe headache |
| `RF_BLEEDING_HEAVY` | urgent | heavy bleeding, vomiting blood, black stool, coughing blood |
| `RF_PREGNANCY_DANGER` | urgent | pregnant plus bleeding, severe abdominal pain, fits, reduced baby movement |
| `RF_CHILD_FEVER` | urgent | age under 3 months with fever; age under 5 with fever plus fits, not feeding, or very sleepy |
| `RF_SEIZURE_FAINT` | urgent | fits, seizure, unconscious, fainted |
| `RF_SELF_HARM` | urgent, **silent** | thoughts of suicide or self-harm (staff alert only; patient sees the neutral message) |
| `RF_TRAUMA_MAJOR` | urgent | road accident, fall from height, head injury with vomiting or drowsiness, deep wound |
| `RF_ALLERGY_SEVERE` | urgent | swelling of lips, face or throat, hives with breathing trouble |
| `RF_POISON_BITE` | urgent | poison, overdose, snake or scorpion bite |
| `RF_SEVERE_PAIN` | priority | pain 9 or 10 out of 10 |
| `RF_HIGH_FEVER` | priority | fever of 104°F (40°C) or more, or described as "very high" |

**Alert behaviour:** an `alert.urgent` message goes to every staff socket of that hospital and department, plays a chime, and shows a banner that stays until a nurse acknowledges it. Unacknowledged alerts flash after 60 seconds and re-notify. Alerts are saved in `red_flag_events` and audited.

## E5. Departments and routing

| Code | Token prefix | Name (en) | Gets |
|---|---|---|---|
| `GENMED` | MED | General Medicine | fever, cough, abdominal, chest, headache, gi_vomit_diarrhoea, urinary |
| `ORTHO` | ORT | Orthopaedics | msk_injury |
| `ENT` | ENT | ENT | ent |
| `OBG` | OBG | Obstetrics and Gynaecology | gyne_pregnancy (females 12 to 50) |
| `PAED` | PED | Paediatrics | any complaint routed to GENMED when age is under 14 |
| `DERM` | DER | Dermatology | skin |
| `OPHTH` | OPH | Ophthalmology | eye |
| `SURG` | SUR | General Surgery | surgical, and abdominal with lump or hernia words |

Add `NURSE_TRIAGE` (a desk, not a department) for `general` fallback or low confidence.

**Confidence:** one module matched: 0.90. Two or more matched: 0.65 and pick the module with the earliest keyword. None: 0.0. Anything under **0.70** sets `needs_nurse_confirmation: true`, and the nurse console shows a "confirm department" prompt. Phase 9 adds an LLM classifier: agreement raises confidence; disagreement forces nurse confirmation.

## E6. Queue: token, score, estimated wait

**Token:** `{prefix}-{NNN}`, three digits, four if above 999. Counter: Redis `INCR osa:{env}:{hospital}:tok:{dept}:{yyyymmdd}` (IST date) with a 36-hour expiry. The database unique constraint is the backstop. On a collision, take the next number.

**Score (lower is served first), the "virtual arrival time":**

```python
URGENCY_BONUS_MS = {"urgent": 6 * 3600 * 1000, "priority": 45 * 60 * 1000, "routine": 0}
VULNERABLE_BONUS_MS = 15 * 60 * 1000   # age >= 65, age < 5, or pregnant

def queue_score(arrival_ms: int, urgency: str, age_years: int | None, pregnant: bool) -> int:
    bonus = URGENCY_BONUS_MS[urgency]
    if pregnant or (age_years is not None and (age_years >= 65 or age_years < 5)):
        bonus += VULNERABLE_BONUS_MS
    return arrival_ms - bonus
```

Worked example (minutes after 10:00): A routine arrives 0 → score 0. B priority arrives 20 → 20 − 45 = −25. C urgent arrives 30 → 30 − 360 = −330. D routine age 70 arrives 10 → 10 − 15 = −5. Served order: **C, B, D, A**. This also prevents starvation: a routine patient who has waited more than 45 minutes outranks a newly arrived priority patient. Constants live in `config.py` so a clinician can tune them.

**Atomic enqueue (Lua):**

```lua
-- KEYS[1] = queue zset, KEYS[2] = pubsub channel
-- ARGV[1] = visit_id, ARGV[2] = score, ARGV[3] = delta json
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
local rank = redis.call('ZRANK', KEYS[1], ARGV[1])
redis.call('PUBLISH', KEYS[2], ARGV[3])
return rank            -- position = rank + 1
```

**Estimated wait:** `eta_minutes = ceil((position - 1) * avg_consult_minutes / max(1, active_doctors))`. `avg_consult_minutes` is the mean of the last 20 completed consults in that department, clamped to 1 to 15, default 4.0 until there are 5 samples. Show a range (0.75× to 1.25×, rounded to 5 minutes), never one false-precise number.

## E7. API contract (`/api/v1`)

Auth column: **K** kiosk token, **P** patient session token, **S** staff token (roles in brackets), **T** status token, **—** public. Every `POST` write accepts `Idempotency-Key`. Lists return `{"items": [], "next_cursor": null, "limit": 50}`.

| Method and path | Auth | Purpose | Rate limit |
|---|---|---|---|
| `POST /auth/login` | — | Staff email and password | 10 per minute per IP |
| `POST /auth/refresh`, `POST /auth/logout` | refresh cookie | Rotate or revoke | 30 per minute per IP |
| `POST /kiosks/activate` | — | Exchange kiosk id and activation code for kiosk token | 10 per minute per IP |
| `POST /intake/sessions` | K | Start a visit and get consent text | 30 per minute per kiosk |
| `POST /intake/sessions/{visit_id}/consent` | P | Record consent | 20 per minute per session |
| `POST /intake/sessions/{visit_id}/identity` | P | Age, sex, optional phone | 20 per minute per session |
| `POST /intake/sessions/{visit_id}/danger-signs` | P | Tiles selected | 20 per minute per session |
| `GET /intake/sessions/{visit_id}/next-question` | P | Next question | 120 per minute per session |
| `POST /intake/sessions/{visit_id}/answers` | P | Submit answer (touch, text or voice transcript) | 120 per minute per session |
| `WS /ws/intake/{visit_id}/audio` | P | Audio streaming (D4) | 4 sockets per kiosk |
| `POST /intake/sessions/{visit_id}/summary` | P | Build draft case sheet and read-back text | 10 per minute per session |
| `POST /intake/sessions/{visit_id}/confirm` | P | Patient confirms or corrects; creates token and queues | 10 per minute per session |
| `GET /status/{status_token}` | T | Position, ETA, state (micro-cached 2 s) | 60 per minute per token, 300 per minute per IP |
| `GET /status/{status_token}/stream` | T | Server-sent events | 4 per token |
| `GET /queue` | S (nurse, doctor, admin) | Paginated queue by department and status | 600 per minute per user |
| `POST /queue/{visit_id}/call`, `/start`, `/complete`, `/no-show` | S (nurse, doctor) | State changes | 600 per minute per user |
| `POST /queue/{visit_id}/override` | S (nurse, admin) | Change urgency or department with reason | 60 per minute per user |
| `WS /ws/staff/queue` | S | Live deltas and alerts (D4) | 2 sockets per user |
| `GET /visits/{id}/case-sheet` | S (doctor, nurse) | Read case sheet (audited) | 600 per minute per user |
| `PATCH /visits/{id}/case-sheet` | S (doctor, nurse) | JSON Patch with `If-Match: version` (409 on conflict) | 120 per minute per user |
| `GET /visits/{id}/case-sheet.pdf` | S (doctor, nurse) | PDF export | 30 per minute per user |
| `GET /visits/{id}/transcript` | S (doctor) | Original transcript | 120 per minute per user |
| `POST /visits/{id}/documents` | P or S | Upload (Phase 12) | 10 per minute per visit |
| `GET/POST/PATCH /admin/{departments,users,kiosks}` | S (admin) | Manage (paginated) | 60 per minute per user |
| `GET /analytics/summary` | S (admin) | Aggregates with date filters | 60 per minute per user |
| `GET /audit` | S (admin) | Cursor-paginated audit log | 30 per minute per user |
| `GET /healthz`, `GET /readyz` | — | Liveness and readiness | none (internal) |
| `GET /metrics` | internal only | Prometheus | network policy |

**Error body (RFC 7807):** `{"type":"about:blank","title":"Too Many Requests","status":429,"code":"RATE_LIMITED","detail":"...","request_id":"..."}` plus a `Retry-After` header for 429. Codes are stable strings such as `VALIDATION_FAILED`, `UNAUTHENTICATED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `RATE_LIMITED`, `STT_UNAVAILABLE`, `LLM_UNAVAILABLE`, `INTERNAL`.

**Cursor pagination:** the cursor is `base64url(json{sort_key, id}) + "." + first 16 bytes of HMAC-SHA256`, so clients cannot tamper with it. Keyset on `(created_at, id)`. Default limit 50, maximum 100. Never use OFFSET.

## E8. Tokens and sessions

| Token | Lifetime | Carries | Stored |
|---|---|---|---|
| Staff access JWT | 10 minutes | `sub`, `role`, `hospital_id`, `jti`, `exp` | Memory only in the browser |
| Staff refresh | 8 hours, rotated on every use, reuse revokes the whole family | opaque random | httpOnly, Secure, SameSite=Strict cookie; hash in DB |
| Kiosk JWT | 12 hours, renewed with the device secret | `sub` (kiosk id), `role=kiosk`, `hospital_id` | Memory and IndexedDB (device secret in a protected store) |
| Patient session JWT | 2 hours | `role=patient`, `visit_id`, `hospital_id` | Memory only; wiped on idle reset |
| Status token | until 24 hours after the visit ends | 128-bit random, URL-safe | Only a hash in DB; shown in the QR code |

Passwords: argon2id. Lockout: 5 failed logins in 15 minutes locks the account for 15 minutes. Roles are checked by a `require_role(...)` dependency; **every route must declare its auth**, and a test iterates the whole route table to enforce it (Phase 2).

---

# Part F — UI and UX design system ("Saathi UI")

The interface must be **beautiful, calm and obvious**. A first-time patient who cannot read well must succeed without help. Build the design system in Phase 1 and reuse it everywhere.

## F1. Design principles

1. **One thing per screen.** One question, one main action.
2. **Voice, icon and text together.** Every prompt is spoken, shown with an icon, and written in the patient's language.
3. **Big and forgiving.** Large touch targets, always a Back, Skip, Change and "Call staff".
4. **Never colour alone.** Urgency, errors and status always have an icon and a word as well.
5. **Fast feels calm.** Skeleton placeholders instead of spinners; feedback within 100 ms; nothing animates longer than 400 ms.
6. **Same layout in every language.** Allow 40% longer text without clipping.
7. **Privacy at rest.** The idle kiosk shows no personal data; after 60 seconds of inactivity a warning appears, and after 15 more seconds the session is wiped.

## F2. Design tokens (put in `styles/tokens.css` as CSS variables and in the Tailwind config)

| Token | Value | Use |
|---|---|---|
| `--navy-900` | `#0B2545` | Headings and main text |
| `--slate-600` | `#475569` | Secondary text |
| `--bg` | `#F7FAFA` | App background |
| `--surface` | `#FFFFFF` | Cards |
| `--border` | `#D5E0E3` | Dividers |
| `--primary` | `#0F766E` | Main buttons, links (white text on it passes AA) |
| `--primary-tint` | `#F0FDFA` | Soft highlights |
| `--urgent` / tint | `#B91C1C` / `#FEE2E2` | Urgent badge, alerts |
| `--priority` / tint | `#B45309` / `#FEF3C7` | Priority badge |
| `--routine` / tint | `#15803D` / `#DCFCE7` | Routine badge, success |
| `--info` | `#1D4ED8` | Info and focus ring |
| Focus ring | 3 px `--info`, 2 px white gap | Every focusable element |

**Typography:** self-host `@fontsource/inter` and `@fontsource/noto-sans-devanagari` (no Google Fonts at runtime, for offline use). Stack: `Inter, "Noto Sans Devanagari", system-ui, sans-serif`. Kiosk base size 20 px (headline 40, sub-headline 30, buttons 24 semibold). Staff base 15 px, tables 14 px. Line height 1.5 for Latin, 1.7 for Devanagari. Never use italics or all-caps for Devanagari.

**Spacing** on a 4 px scale (4, 8, 12, 16, 24, 32, 48, 64). **Radius:** 12 px cards, 16 px kiosk buttons, full for chips. **Shadow:** `0 1px 2px rgba(11,37,69,.06), 0 8px 24px rgba(11,37,69,.08)`. **Motion:** 150 to 250 ms ease-out; respect `prefers-reduced-motion` (fade only). **Viewports to support:** kiosk landscape 1280×800 (primary), tablet portrait 768×1024, phone 390×844, staff desktop from 1366×768.

## F3. Components to build (all in `components/ui`, each with loading, empty, error and disabled states where relevant)

| Component | Notes |
|---|---|
| `Button` | primary, secondary, danger, ghost; sizes `kiosk` (72 px high), `md` (48), `sm` (36); icon slot; busy state |
| `LanguageTile` | Native script name, large, with a small icon |
| `DangerTile` | Icon plus label, toggles selected state with check mark |
| `ChoiceButton` and `ChipGroup` | Single and multiple choice, big hit areas |
| `NumberPad` | Large on-screen digits for age and phone |
| `FacesScale` | 0 to 10 pain scale with faces and numbers |
| `VoiceOrb` | States: idle (slow pulse), listening (bars follow input level), thinking (three dots), speaking (ripple), error (shake plus icon). Always labelled with text |
| `TranscriptBubble` | Live text; tap to edit; "Is this right?" with a check and a pencil |
| `ProgressDots` | "Step 4 of 9", with a text label for screen readers |
| `ReadAloudButton` | On every screen; plays the prompt audio |
| `HelpButton` | "Call staff" in every kiosk screen header |
| `TokenCard` | Token label at 96 px, department, QR code, position, estimated wait |
| `UrgencyBadge` | Urgent (triangle-alert icon), Priority (clock icon), Routine (circle icon); colour plus icon plus word |
| `ConfidenceChip` | Yellow below 0.70, with a tooltip "Please check" |
| `CaseSheetSection` | Titled block with source icon (voice, touch, nurse, doctor, document) per field |
| `QueueRow`, `DataTable` | Virtualised for thousands of rows |
| `AlertBanner` | Sticky, red, needs acknowledgement, flashes after 60 s |
| `ConnectionIndicator` | Live (green), Reconnecting (amber), Offline (grey) |
| `OfflineBanner`, `IdleWarningDialog`, `ConfirmDialog`, `Toast`, `Skeleton`, `EmptyState`, `ErrorState`, `StatCard` | Standard |

Provide a dev-only gallery route `/dev/components` that shows every component in all states and all three languages. It is the human's quickest way to judge the design.

## F4. Kiosk screens

| # | Screen | Purpose and key elements | Voice prompt id |
|---|---|---|---|
| S1 | Welcome and language | Hospital name, three `LanguageTile`s (हिन्दी, मराठी, English), `HelpButton` | `ui.welcome` |
| S2 | Consent | 60 words or less, `ReadAloudButton`, "I agree" and "I do not agree — call staff" | `ui.consent` |
| S3 | Danger signs | Eight `DangerTile`s plus a large "None of these"; headline "Do you have any of these right now?" | `ui.danger` |
| S4 | Basic details | Age with `NumberPad`, three sex buttons, optional phone with "Skip" | `ui.details` |
| S5 | Interview | `ProgressDots`, question text, `VoiceOrb`, alternative "Use touch or type", live `TranscriptBubble`, Skip and Back | one per question id |
| S6 | Summary and confirm | Sections each with a big "Correct" and "Change"; `ReadAloudButton`; final "Yes, this is correct" | `ui.confirm` |
| S7 | Token | `TokenCard`, "Scan on your phone", "Wait nearby, we will call your number", print button (80 mm receipt layout); auto-reset after 45 seconds and wipe the session | `ui.token` |

## F5. Staff screens

- **Login:** email and password, clear error text, lockout message.
- **Queue board:** tabs per department; urgent cards pinned on top with a red left bar; columns: token, age and sex, one-line complaint, `UrgencyBadge`, waiting time, status; `ConnectionIndicator` and alert bell in the top bar.
- **Doctor consult view:** case sheet on the left (one page, sections in the order doctors read: complaint, duration and severity, associated symptoms, red flags, history, medications, allergies), original transcript on the right, actions at the bottom (Start, Done, Edit). Keyboard shortcuts: `N` next, `E` edit, `P` print or PDF.
- **Nurse console:** assisted registration (same engine as the kiosk), alerts panel, override dialog (reason required), call next, no-show.
- **Admin:** users, kiosks (generate activation code), departments, analytics, audit log.

## F6. Patient status page (phone, `/s/:token`)

Huge number: "4 people ahead of you" or "You are next". Estimated wait as a range. Token and department. "Please stay nearby." Language switch. Live updates through server-sent events with automatic reconnect. When called: a green full-screen "Please go to the doctor's room now." Footer: "Need help? Tell the nurse."

## F7. Accessibility and internationalisation rules

- WCAG 2.2 AA: contrast of at least 4.5:1 for text, visible focus, full keyboard use on staff screens, `aria-live="polite"` for transcripts and queue changes, `aria-live="assertive"` for urgent alerts.
- Touch targets: at least **64 px** on the kiosk and **44 px** elsewhere.
- Text in JSON files only (`locales/{en,hi,mr}/*.json`); no hardcoded strings. A script fails CI if a key exists in `en` and is missing in `hi` or `mr`.
- A pseudo-locale (text 40% longer) must render without clipping.
- Optional high-contrast mode and larger-text toggle in the kiosk header.
- Numbers use Latin digits by default, with a setting for Devanagari digits.

## F8. Microcopy rules

Short sentences (12 words or fewer), verbs first, no medical jargon, no blame ("Let's try again", never "You made an error"), no alarming words for the patient, same word for the same thing everywhere. Keep a glossary at `locales/glossary.md` with the approved Hindi and Marathi terms. A native speaker must review all Hindi and Marathi text before the demo.

## F9. UI quality gates (apply to every UI phase)

- `@axe-core/playwright`: **zero** serious or critical violations on every screen.
- Lighthouse (mobile profile) on kiosk and status pages: Performance at least 85, Accessibility at least 95, Best Practices at least 95; layout shift under 0.1; installable as a PWA.
- Playwright bounding-box test: every interactive element on the kiosk is at least 64 px in height and width.
- Screenshots for 3 languages × 3 viewports saved to `docs/screenshots/`.
- Pseudo-locale test passes.

---

# Part G — Engineering standards

## G1. Security checklist (verify in Phase 2 and Phase 13)

| Control | Where it lives | How it is tested |
|---|---|---|
| Deny-by-default auth on every route | `require_role` dependency | Route-matrix test: anonymous call to every route must be 401 or 403 unless explicitly public |
| JWT hardening | `core/security.py` | Tests for `alg: none`, wrong key, expired, wrong audience, tampered claims |
| Password and lockout | argon2id, 5 failures lock 15 minutes | Unit and integration tests |
| Refresh rotation | one-time refresh tokens, family revoke on reuse | Reuse test |
| Tenant isolation | Postgres RLS | Two-hospital test: hospital A can never read B, even with a guessed id |
| Object-level access | Patient token only sees its own visit | IDOR tests |
| Input validation | Pydantic models with max lengths | Fuzz with schemathesis; 1 MB body limit (8 MB for uploads) |
| Output encoding | React escapes by default; no `dangerouslySetInnerHTML` | ESLint rule plus test |
| Security headers | Strict CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `Permissions-Policy: microphone=(self)` | Header test |
| CORS | Allow-list from env | Test with a foreign origin |
| Field encryption | AES-256-GCM `v1:` | Raw SQL test finds no plaintext |
| Secrets | Environment only; `.env` ignored by git; gitleaks in CI | CI |
| PII in logs | Redaction processor in structlog | Test logs a fake phone and name, asserts masked |
| PII to LLM | `redact_pii()` on every outbound text | Unit tests (phones, ABHA numbers, 12-digit IDs, emails) |
| Prompt injection | Patient text wrapped as data, schema-validated output, no tools, post-filter | Adversarial eval set (Phase 9) |
| Audit | Every read of a case sheet or transcript, every override, login and export | Test asserts rows exist |
| Dependency hygiene | `pip-audit`, `npm audit`, Semgrep, Bandit in CI | CI must pass |

## G2. Caching (what, where, how long)

| Layer | What | TTL or rule |
|---|---|---|
| Browser (service worker) | App shell, fonts, locale files, prerecorded prompt audio | Precache; versioned by content hash |
| CDN (production) | The same static assets | Immutable, 1 year, hashed filenames |
| Redis | Question bank `osa:{env}:qb:{hash}` | 1 hour, versioned key |
| Redis | Department and doctor lists `osa:{env}:{hospital}:depts` | 5 minutes, delete on change |
| Redis | Patient status micro-cache `osa:{env}:{hospital}:st:{token_hash}` | 2 seconds; also pushed over SSE on change |
| Redis | Interview state `osa:{env}:iv:{visit_id}` and session `osa:{env}:sess:{visit_id}` | 2 hours |
| Redis | Idempotency `osa:{env}:idem:{key}` | 24 hours |
| Redis | Generic LLM output cache `osa:{env}:llm:{sha256}` | 7 days; **only** for fixed text such as translations and rephrasings; **never** keyed by patient text |
| Postgres | Analytics materialised views | Refresh every 5 minutes |
| HTTP | `ETag` plus `Cache-Control: private, max-age=0, must-revalidate` for API reads; gzip or brotli | Always on |

Cache rule: **a cache miss must never change correctness**, only speed.

## G3. Pagination and data transfer

- Keyset (cursor) pagination for every list: patient history, audit log, queue lists, admin tables. Default 50, maximum 100. Cursor format in E7.
- The UI uses virtualised lists (`@tanstack/react-virtual`) and loads the next page near the end.
- Realtime sends **deltas** with sequence numbers, never the whole queue (except the first snapshot).
- Endpoints accept `fields=` to limit returned columns for heavy lists.
- Test: page through 10,000 rows while inserting new rows; no duplicates and no gaps.

## G4. Rate limiting and resilience

**Limiter:** Redis sliding-window Lua script. Keys `osa:{env}:rl:{name}:{subject}`. Return headers `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and on 429 `Retry-After`. Limits are in the E7 table; also enforce:

| Name | Limit |
|---|---|
| OTP requests | 5 per hour per phone |
| Failed logins | 5 per 15 minutes per account |
| LLM calls | 30 per visit session |
| Speech clips | 60 seconds per utterance, 4 sockets per kiosk |
| Uploads | 10 per visit, 8 MB each |

**Resilience defaults (apply to every outbound call):**

| Pattern | Default |
|---|---|
| Timeouts | connect 1 s; speech final 2 s; LLM 2.5 s; TTS 2 s; database statement timeout 5 s |
| Retries | 1 retry with 100 to 300 ms random jitter, only for idempotent calls |
| Circuit breaker | opens after 5 failures in 30 s; half-open after 20 s; open state goes straight to fallback |
| Bulkheads | separate client pools and semaphores per provider (STT 50, LLM 50, TTS 20) |
| Backpressure | cap WebSocket accepts; reject new audio sockets with 503 when saturated |
| Load shedding | when p95 exceeds 2× budget for 30 s, shed analytics and exports first |

## G5. Idempotency, validation, errors

- Every `POST` that creates or changes something accepts `Idempotency-Key` (UUID). First call stores the response in Redis for 24 hours; a repeat returns the stored response.
- Request and response models are Pydantic with `extra="forbid"`, explicit max lengths and enums.
- One global exception handler returns problem+json with `request_id`. Stack traces never reach clients.

## G6. Observability

- **Logs:** JSON (structlog) with `request_id`, `hospital_id`, `route`, `status`, `duration_ms`. No personal data.
- **Metrics (Prometheus):** `http_request_duration_seconds`, `stt_latency_seconds{provider}`, `llm_latency_seconds{provider}`, `tts_latency_seconds`, `queue_depth{dept}`, `ws_connections{type}`, `rate_limited_total`, `circuit_open{provider}`, `outbox_oldest_age_seconds`, `urgent_alert_ack_seconds`.
- **Traces (OpenTelemetry):** FastAPI, SQLAlchemy, Redis and outbound HTTP, with the voice turn as one trace.
- **SLOs to document:** voice turn p95 under 1.5 s; queue update p95 under 200 ms; API availability 99.9% (target); urgent alert delivered under 1 s.
- Grafana dashboard JSON committed to `infra/grafana/`.

## G7. Testing strategy

| Layer | Tool | Target |
|---|---|---|
| Unit (domain, core) | pytest, Hypothesis | **95%** coverage on `app/domain` and `app/core` |
| Integration (API, DB, Redis) | pytest-asyncio, httpx, real Postgres and Redis in Compose | 80% overall backend coverage |
| Contract | schemathesis against the OpenAPI document | No 5xx on fuzzed input |
| Frontend unit | Vitest, Testing Library | Components with logic |
| End to end | Playwright with `@axe-core/playwright` | Every main flow, 3 languages |
| Evaluation (accuracy) | scripts in `eval/` | Gates in Part I |
| Load | k6 | Gates in Phase 13 |
| Security | ZAP baseline, Semgrep, Bandit, pip-audit, npm audit, gitleaks | Zero high findings |

Rules: tests never call real paid providers in CI (use Mocks); real-provider evals run manually with `make eval-live`. Flaky tests are fixed or removed with an ADR, never retried silently.

## G8. Performance budgets (reference machine: 4 vCPU, 8 GB, local Docker, mock providers)

| Item | Budget (p95 unless noted) |
|---|---|
| Next-question API (deterministic) | 150 ms |
| Answer submit | 200 ms |
| Enqueue and token issue | 80 ms server time |
| Patient status GET | 100 ms (cache hit 20 ms) |
| Queue page of 50 | 150 ms |
| Case sheet read | 150 ms |
| PDF export | 1.5 s |
| Realtime delta delivery | 200 ms |
| Voice turn (end to end) | 1.5 s |
| Web LCP on kiosk shell | 2.5 s; INP 200 ms |
| Initial JavaScript (gzipped) | kiosk 250 KB; staff 350 KB |

## G9. Code standards

- Business rules live in `app/domain` as **pure functions** (no I/O), which keeps them easy to test and explain.
- Database access only through repository functions; no raw SQL in routers; every query filtered by the RLS context.
- Async everywhere on request paths; never block the event loop (CPU-heavy work goes to the worker).
- Migrations are small, reversible, and never destructive without an ADR. Test `upgrade` then `downgrade` then `upgrade`.
- Configuration through `pydantic-settings` only; every setting documented in Appendix A.
- Docstrings explain **why**, not what. Every module with clinical meaning states its safety assumptions.

---

# Part H — Build phases

Every phase uses the same sections: **In plain words**, **Build steps**, **Automated tests**, **Manual test for the human**, **Gate**, **Pitfalls**. Build steps are numbered `N.k`; finish and test each before starting the next.

## Phase 0 — Foundations and tooling

**Features:** none (setup) · **Depends on:** nothing · **Branch:** `phase-00-foundations`

> **In plain words:** We set up the workshop: tools, an empty but running app, and automatic checks. Later phases then go fast and stay safe, because every change is checked automatically.

**Build steps**

- 0.1 Ask the human which operating system they use and whether Docker, Node (LTS) and Python 3.12 are installed. Record in the README. *(manual only)*
- 0.2 Create the repository layout from C4, `.gitignore`, `.editorconfig`, and `.env.example` (Appendix A). Test: `gitleaks` finds nothing.
- 0.3 `docker-compose.yml` with `postgres:16` and `redis:7` (password required, healthchecks) plus `api` and `worker` built from one multi-stage `services/api/Dockerfile` (`python:3.12-slim`, non-root user). Pin images to major.minor, never `latest`. Test: `docker compose up -d` becomes healthy within 60 seconds.
- 0.4 Backend skeleton: `create_app()` factory, `pydantic-settings` config (two Redis URLs, `REDIS_STATE_URL` and `REDIS_CACHE_URL`, pointing at the same Redis in development), structlog JSON logging with a request-id middleware, problem+json error handlers, security-header middleware, CORS allow-list, `/healthz` (process alive) and `/readyz` (checks Postgres `SELECT 1` and Redis `PING`; returns 503 naming the failing dependency). Run with gunicorn and the `uvicorn-worker` package. Tests: `test_health`, `test_error_shape`, `test_headers`.
- 0.5 Database layer: async SQLAlchemy engine and session, Alembic initialised, migration `0001` creates `hospitals` and the limited role `osa_app`. Test: upgrade, downgrade, upgrade.
- 0.6 Frontend skeleton: Vite, React, TypeScript strict, Tailwind, shadcn/ui init, React Router with placeholder routes `/kiosk`, `/staff`, `/s/:token`, `/dev/components`, TanStack Query provider, i18next with empty `en`, `hi`, `mr` files, `vite-plugin-pwa` (no caching rules yet), ESLint, Prettier, Vitest. Test: `npm run build` succeeds; a Vitest smoke test renders the app.
- 0.7 Types bridge: `make types` generates `apps/web/src/lib/api-types.ts` from the API's OpenAPI document using `openapi-typescript`. CI fails if the committed file is out of date.
- 0.8 `Makefile` with every target in Appendix B. Test: each target runs.
- 0.9 CI workflow (GitHub Actions): backend (ruff, mypy, pytest against Postgres and Redis services), frontend (eslint, tsc, vitest, build), security (gitleaks, pip-audit, `npm audit --audit-level=high`, bandit, semgrep), and the types drift check.
- 0.10 Docs: README quickstart, `docs/DECISIONS.md` with ADR 001 (stack), ADR 002 (MVP versus production mapping from C2), and `docs/phase-reports/`.

**Automated tests:** health, error shape, headers, migration cycle, frontend smoke, CI itself.

> **Manual test:** run `make up`, then `make api`; open `http://localhost:8000/docs` and `/readyz` (should say healthy). Run `make web`; open `http://localhost:5173` and see the empty app shell. Stop the Redis container; `/readyz` must return 503 and name `redis`; start it again and it returns 200.

> **Gate:** CI green · `make test` passes · a fresh clone reaches the running app in 10 minutes or less using only the README · zero secrets found · `/readyz` degradation test passes.

**Pitfalls:** do not add features; do not use `latest` image tags; never commit `.env`.

---

## Phase 1 — UI foundation and language selection (F1)

**Depends on:** 0 · **Branch:** `phase-01-ui-foundation`

> **In plain words:** We build the look and feel: colours, fonts, big friendly buttons, and the first screen where a patient picks a language. Every later screen reuses these parts, so the product looks polished and consistent. You will see the whole design early, using preview screens with fake data.

**Build steps**

- 1.1 Implement the tokens from F2 in `styles/tokens.css` and the Tailwind theme; self-host the fonts. Test: a script computes the WCAG contrast ratio of every text and background pair and fails below 4.5.
- 1.2 Build the components from F3 (first batch: `Button`, `LanguageTile`, `DangerTile`, `ChoiceButton`, `ChipGroup`, `NumberPad`, `FacesScale`, `ProgressDots`, `UrgencyBadge`, `ConfidenceChip`, `HelpButton`, `ReadAloudButton` (stub), `ConnectionIndicator`, `OfflineBanner`, `IdleWarningDialog`, `ConfirmDialog`, `Toast`, `Skeleton`, `EmptyState`, `ErrorState`, `StatCard`). Use CSS transitions only (no animation library). Test: Vitest per component for states and keyboard use.
- 1.3 Dev-only gallery `/dev/components` that shows every component in every state and all three languages.
- 1.4 i18n plumbing: namespaces `common`, `kiosk`, `staff`, `status`; language store in Zustand (kept only for the session); a pseudo-locale `?lang=xa` that makes text 40% longer; a CI script `check-locales` that fails on missing keys.
- 1.5 Kiosk shell: header (hospital name, language switch, `HelpButton`, text size and high-contrast toggles), idle timer (warning at 60 s, reset 15 s later, wipes state), and an `AudioPrompt` component that plays `/audio/{lang}/{id}.mp3` when present and does nothing otherwise (real audio arrives in Phase 10).
- 1.6 Screen S1 (welcome and language) fully styled. Screens S2 to S7 as **static preview screens with fake data** at `/kiosk/preview/s2` … `/s7` (dev only), so the whole design can be judged now.
- 1.7 Staff shell (left navigation, top bar) and a styled, not yet wired, login page. Patient status page UI with mock data at `/s/demo`.
- 1.8 PWA: precache the app shell and fonts only.
- 1.9 Playwright: screenshots for every screen × 3 viewports × 3 languages, axe checks, tap-target check (kiosk 64 px), pseudo-locale check, keyboard walk-through of the staff login.
- 1.10 Lighthouse CI configuration with the thresholds in F9.

**Automated tests:** component tests, contrast script, locale check, Playwright (axe, tap targets, pseudo-locale, screenshots), Lighthouse CI.

> **Manual test:** open `/dev/components` and look at everything. Open `/kiosk`, tap each language, and confirm Hindi and Marathi letters look correct. Open `/kiosk/preview/s2` to `/s7` and judge the design. Open the site on your phone using your laptop's LAN address. Press `Tab` repeatedly on the staff login page. Zoom the browser to 200%. Tell the agent what you want changed (colours, spacing, wording) before approving; this is the cheapest moment to change the design.

> **Gate:** axe shows **0** serious or critical violations on every screen · all colour pairs at least 4.5:1 · all kiosk tap targets at least 64 px · Lighthouse mobile Performance at least 85, Accessibility at least 95, Best Practices at least 95 · layout shift under 0.1 · pseudo-locale shows no clipping · locale check passes · kiosk initial JavaScript at most 250 KB gzipped.

**Pitfalls:** no hardcoded strings; do not call the backend yet; do not load fonts from the internet.

---

## Phase 2 — Identity, audit and rate limiting (F11 staff login, F15 security core)

**Depends on:** 0, 1 · **Branch:** `phase-02-identity-security`

> **In plain words:** We build the front door and the security guard. Staff log in, each role can do only its own tasks, every sensitive action is written in a diary that nobody can alter, and anyone hammering the system gets slowed down.

**Build steps**

- 2.1 Migrations: `users`, `kiosks`, `refresh_tokens`, `audit_log`; `REVOKE UPDATE, DELETE ON audit_log FROM osa_app`; enable and force RLS with policies on every tenant table created so far. Test: an `UPDATE` on `audit_log` as `osa_app` fails with permission denied.
- 2.2 `core/security.py`: argon2id hashing; JWT encode and decode with `kid`, claims per E8, 30 s clock skew, 10-minute access tokens. Tests: valid, expired, wrong key, `alg: none`, tampered claim, wrong role.
- 2.3 RLS context: each request transaction runs `SET LOCAL app.hospital_id`. Test (two hospitals): user A cannot read or write hospital B rows; a raw query without context returns zero rows.
- 2.4 `POST /auth/login`, `/auth/refresh` (one-time rotation, reuse revokes the family), `/auth/logout`, lockout after 5 failures for 15 minutes. Tests: success, wrong password, lockout, refresh reuse.
- 2.5 Dependencies `require_role`, `require_kiosk`, `require_patient`. **Route-matrix test:** iterate every route in the app; each non-public route must return 401 without a token and 403 with a wrong-role token. Public routes are listed in one `PUBLIC_ROUTES` allow-list.
- 2.6 Rate limiter: Redis sliding-window Lua script, `rate_limit()` dependency, headers `X-RateLimit-*` and `Retry-After`. Tests: the 11th login in 60 s returns 429; with Redis stopped, login **fails closed** (503) and other limited routes fall back to the in-process limiter.
- 2.7 Kiosk activation: CLI `python -m app.cli create-kiosk` prints a one-time activation code; `POST /kiosks/activate` returns a kiosk JWT. Test: wrong code, reused code.
- 2.8 `audit()` helper writing `audit_log`; `GET /audit` (admin, cursor-paginated). Test: login success, failure, lockout and kiosk activation each create a row.
- 2.9 Frontend: typed API client with in-memory access token and a single-flight refresh interceptor; protected routes; role-aware navigation; session-expired screen; wire the login page. Playwright: login, wrong password message, lockout message, logout.
- 2.10 Security middleware polish: strict CSP in the proxy and app, HSTS, `Permissions-Policy: microphone=(self)`, body size limits. Test: header assertions.

**Automated tests:** the items above, plus `bandit` with zero high findings.

> **Manual test:** run `python -m app.cli create-admin` and log in at `/staff`. Enter a wrong password 6 times and read the lockout message. Call the login endpoint 11 times quickly (a script `make demo-ratelimit` is provided) and see HTTP 429 with `Retry-After`. Open `GET /audit` as admin and see your attempts listed.

> **Gate:** route-matrix test passes for 100% of routes · all token failure tests pass · two-hospital isolation passes · app role cannot update or delete audit rows · rate-limit tests pass · coverage for `core/security.py` and `core/ratelimit.py` at least 95% · bandit high findings = 0.

**Pitfalls:** never log tokens or passwords; do not store the access token in `localStorage`; do not trust a role sent by the client.

---

## Phase 3 — Data model, seeds and pagination (F5 schema)

**Depends on:** 2 · **Branch:** `phase-03-data-model`

> **In plain words:** We build the filing cabinet. Tables for patients, visits and case sheets; sensitive fields are locked with encryption; a demo hospital is filled in; and long lists load page by page so they stay fast even with hundreds of thousands of rows.

**Build steps**

- 3.1 Migrations for `departments`, `patients`, `consents`, `visits`, `case_sheets`, `interview_turns`, `red_flag_events`, `outbox`, `notifications`, `documents` with the indexes, uniqueness and RLS from E1. Test: upgrade, downgrade, upgrade.
- 3.2 `core/crypto.py`: AES-256-GCM `v1:` encryption, HMAC lookup hash, key loading from env. Tests: round trip; tampered ciphertext fails; wrong associated data fails; 100,000 encryptions use unique nonces; a raw SQL scan finds no plaintext name, phone or ABHA.
- 3.3 Pydantic case sheet models from E2 with validators; export `docs/case_sheet.schema.json`; CI fails if it is stale. Tests: 20 valid and 20 invalid examples (unknown field, oversize, bad enum, bad pointer).
- 3.4 Async repositories for patients, visits, case sheets, turns, departments. No SQL in routers.
- 3.5 Pagination utility: signed cursor, keyset on `(created_at, id)`, `Page[T]` envelope. Tests: page through 10,000 rows while rows are inserted, with no duplicates and no gaps; a tampered cursor returns 400 `VALIDATION_FAILED`.
- 3.6 Idempotency dependency using Redis. Tests: same key twice creates one row and returns the same response; same key with a different body returns 422 `IDEMPOTENCY_KEY_REUSED`.
- 3.7 Seeds: `seed-demo` creates one fictional hospital ("Demo Government Hospital, Nagpur"), 8 departments with Hindi and Marathi names (E5), 12 doctors, 3 nurses, 1 admin, 2 kiosks. `seed-bulk --visits 100000` generates synthetic visits with a fixed random seed. Credentials come from env and are printed once to the console, never logged.
- 3.8 Admin endpoints for departments, users and kiosks, paginated and audited.
- 3.9 Query plan tests: with 100,000 visits, the queue query uses the index and runs under 20 ms at p95; save the plans in the phase report.

> **Manual test:** run `make seed`, then open `/docs` and call the admin list endpoints with `limit=5` and follow `next_cursor` until it ends. Run `make seed-bulk` and confirm lists still respond quickly. Open the database with any SQL client and look at `patients`: names and phones must look like gibberish.

> **Gate:** encryption tests pass · no plaintext in the database · RLS passes on all new tables · pagination test passes with concurrent inserts · query plan p95 under 20 ms on 100,000 visits · migrations reversible · coverage for `core/*` at least 95%.

**Pitfalls:** do not put encryption keys in the repo; do not use OFFSET pagination; do not return encrypted values to clients.

---

## Phase 4 — Deterministic interview and case sheet (F2 consent, F4 core, F5)

**Depends on:** 3 · **Branch:** `phase-04-deterministic-interview`

> **In plain words:** We build the entire interview using buttons and typing only: no AI and no voice. It already works from start to finish, which proves the flow. Later, this same engine becomes the safety net whenever AI or the network fails.

**Build steps**

- 4.1 Consent: `consent/{lang}.md` (60 words or fewer; drafts marked for review). `POST /intake/sessions` (kiosk token) creates a visit with status `registering` and returns a patient session token and the consent text; `POST …/consent` stores the version and SHA-256 of the shown text. All other intake routes return 409 `CONSENT_REQUIRED` until consent is recorded.
- 4.2 `identity` (age 0 to 120, sex, optional phone matching `^[6-9]\d{9}$`, stored encrypted plus HMAC) and `danger-signs` (validated tile ids; any tile creates a `red_flag_events` row and sets urgency to urgent).
- 4.3 Question bank: `core.json`, the 13 modules and `triggers.json` in English, Hindi and Marathi (E3), plus the bank validator wired into CI. Tests: the validator passes on the real bank and fails on deliberately broken fixtures.
- 4.4 Interview engine as pure functions in `domain/interview/engine.py`: `start`, `next_question`, `apply_answer`, `build_case_sheet`. Tests: unit tests plus Hypothesis (500 random valid answer sequences always end within 12 questions, never repeat, and always produce a case sheet that validates).
- 4.5 Persistence: state in Redis (2-hour TTL) mirrored into `interview_turns`; endpoints `next-question` and `answers` with idempotency; input cleaning (strip control characters, maximum 500 characters). Test: repeating the same answer key creates one turn.
- 4.6 `summary` builds the draft case sheet and a plain read-back text from per-language templates (no AI). Test: golden outputs.
- 4.7 `confirm` records the patient's confirmation and sets status `awaiting_confirmation`; it returns a stub token response until Phase 5.
- 4.8 Kiosk screens S2 to S7 wired to the API: consent, danger tiles, details, interview with every touch input type, summary with a Change flow that re-opens that question, and a token screen showing a stub. The `VoiceOrb` is visible but disabled with the note "Voice arrives soon". An idle reset calls `DELETE /intake/sessions/{id}` and revokes the patient token.
- 4.9 Golden evaluation: `eval/interview/golden/` holds **30 scripted interviews** (10 English, 10 Hindi, 10 Marathi) with the expected case sheet each. `make eval-interview` compares them.
- 4.10 Playwright: a full flow in each language, a flow with a danger tile, back and skip use, and axe checks.

**Automated tests:** unit, Hypothesis, golden, API integration, Playwright in three languages.

> **Manual test:** open `/kiosk`, choose Marathi, complete an interview by touch for "fever for three days". Repeat in Hindi tapping a danger tile. At the end read the summary and check that the case sheet in the admin or API view matches what you entered. Try typing very long text, emoji and `<script>` as an answer; the system must stay calm.

> **Gate:** golden set **30 of 30** pass · Hypothesis suite passes · Playwright passes in 3 languages · typical complaint finishes in 25 taps or fewer and a scripted 4-seconds-per-answer run finishes in 180 seconds or less · malicious input (10,000 characters, HTML, SQL strings, null bytes, emoji) handled with 400 or safe cleaning · consent-required test passes · coverage for `domain/interview` at least 95%.

**Pitfalls:** no AI calls in this phase; every question must exist in all 3 languages; never let the engine ask more than 12 questions.

---

## Phase 5 — Queue, token and live position (F9)

**Depends on:** 4 · **Branch:** `phase-05-queue`

> **In plain words:** We build the token machine and the live board. After confirming, a patient gets a number and can watch their place in line change in real time. A nurse can call the next person. If Redis restarts, the order is rebuilt from the permanent record, so nobody loses their place.

**Build steps**

- 5.1 `domain/queue/scoring.py` with the score function from E6 and constants in `config.py`. Tests: table-driven (25 cases) including the worked example, which must order **C, B, D, A**.
- 5.2 Token allocation: Redis `INCR` counter per department per IST day with the database unique constraint as backstop. **If Redis is down**, fall back to the database using `pg_advisory_xact_lock(hash(hospital, dept, date))` and `MAX(token_number)+1`. Tests: 1,000 concurrent allocations give unique tokens; the fallback path gives unique tokens too.
- 5.3 Queue service with Lua scripts for enqueue, reprioritise and remove (atomic, publishes a delta). Tests against real Redis.
- 5.4 Outbox plus `outbox_relay` worker (arq): process unprocessed rows every 2 seconds, idempotently. Test: stop Redis during an enqueue, restart it, and the order is correct after the relay runs.
- 5.5 Rebuild: `python -m app.cli queue-rebuild`, and automatic rebuild at API start when the sorted sets are missing. Test: flush Redis, rebuild, and the order equals the database-derived expected order.
- 5.6 `confirm` now creates the token and queues the visit. Until Phase 6, use `route_stub()`: department `GENMED`, or `PAED` when age is under 14; urgency from danger tiles only.
- 5.7 Status token and endpoints: `GET /status/{token}` with the 2-second micro-cache, `GET /status/{token}/stream` (SSE), ETA estimator from E6 (unit tested), and **one** visit state-machine function enforcing allowed transitions (`queued → called → in_consult → done`; `queued` or `called → no_show`; any → `cancelled` by a nurse). Invalid transition returns 409.
- 5.8 Staff endpoints: `GET /queue` (cursor), `call`, `start`, `complete`, `no-show`, `override` (reason required, audited, re-scores); `WS /ws/staff/queue` with the auth message, snapshot, `seq` deltas and ping/pong (D4). Test: a missed `seq` makes the client refetch the snapshot.
- 5.9 Frontend: kiosk S7 with a real `TokenCard` (QR code from `qrcode.react` pointing to `/s/{token}`) and a receipt print layout; patient status page wired to SSE with reconnect backoff (1, 2, 4, 8 s, max 15 s) and a 5-second polling fallback; a minimal staff queue board with Call next and live updates.
- 5.10 `make simulate-queue N=50 URGENT=10` creates synthetic visits through the real API so the human can watch the system work.

**Automated tests:** scoring table, property test (the served order always equals the sorted-by-score order), concurrency, rebuild, relay recovery, ETA, state machine (random valid transitions never reach an invalid state), WebSocket `seq`, SSE reconnect, and a k6 smoke test (500 enqueues).

> **Manual test:** run `make simulate-queue`, open `/staff` (queue board) and a status page `/s/<token>` on your phone. Click Call next and watch positions change within about a second. Stop and restart Redis (`docker compose restart redis`) and confirm the board returns to the same order.

> **Gate:** worked example orders C, B, D, A · property test passes · no starvation test (a routine patient waiting over 45 minutes outranks a newly arrived priority patient) · 1,000 parallel enqueues give unique tokens and consistent positions · flush and rebuild gives identical order · outbox recovery passes · enqueue p95 at most 80 ms · status GET p95 at most 100 ms at 500 requests per second · realtime delta delivery p95 at most 200 ms · coverage for `domain/queue` at least 95%.

**Pitfalls:** Redis is a read model, never the truth; do not compute position with SQL on each request; never show the word "urgent" to a patient; mask status tokens in logs.

---

## Phase 6 — Red flags and department routing (F7, F8)

**Depends on:** 5 · **Branch:** `phase-06-redflags-routing`

> **In plain words:** We build the bell and the signpost. The bell rings for the nurse when danger words or danger signs appear. The signpost sends the patient to the right department. Both are plain rules, so they are fast, explainable and testable. AI comes later and may only make the bell more sensitive, never less.

**Build steps**

- 6.1 `normalize.py` (E4) with 40 unit tests including Devanagari variants (nukta forms, chandrabindu versus anusvara), Hinglish spellings and Unicode NFKC.
- 6.2 Rule schema, loader and validator: every rule has `id`, `level`, `status`, `reviewed_by`, `reason_en`, groups, and at least 3 positive and 2 negative examples in `eval/redflags/unit_examples.yaml`. CI fails if a rule lacks examples.
- 6.3 Rule engine `evaluate()` with group matching, structured conditions (age, sex, pregnancy, pain score, tiles) and the negation window from E4. Overall urgency is the maximum. Deterministic output order.
- 6.4 Write the starter rule set from E4 in English, Hindi, Marathi and Hinglish.
- 6.5 Routing engine from E5 with age and sex modifiers, confidence and `NURSE_TRIAGE`. Tests: 60 table-driven cases.
- 6.6 Integrate: evaluate rules after each answer and at summary time; update visit urgency; write `red_flag_events` (deduplicated per rule); re-score the queue; show patients only the neutral message. Self-harm flags have `visible_to_patient = false`.
- 6.7 Alert pipeline: `alert.urgent` over the staff socket, `GET /alerts?acknowledged=false`, `POST /alerts/{id}/ack`, escalation (flash and re-notify) after 60 seconds, a chime in the staff app behind an "Enable alert sound" toggle (browsers need a click first), and the `AlertBanner`.
- 6.8 Datasets: `eval/redflags/scripts.jsonl` with **at least 200** synthetic labelled scripts (English 80, Hindi 60, Marathi 40, Hinglish 20) including **60 `must_catch`**, 50 negation or near-miss cases and 30 benign cases; `eval/routing/cases.jsonl` with at least 150 cases. Each item has `split: dev` (70%) or `split: test` (30%). Tune rules on `dev` only and report both.
- 6.9 `make eval-redflags` and `make eval-routing` print a confusion matrix, per-rule recall and precision, and the list of misses; results are written to `docs/EVALUATION.md`.
- 6.10 Replace `route_stub()` in `confirm` with the real routing; add a minimal override control to the queue board.

> **Manual test:** on the kiosk, say or type "chest pain and sweating" (or tap the chest tile). The nurse queue board should show a red alert banner within one second and the patient should jump forward. Acknowledge the alert. Type "no chest pain" and confirm no alert appears. Open `docs/EVALUATION.md` and read the numbers.

> **Gate:** red-flag recall at least **98%** overall and **100%** on the must-catch subset (60 of 60), on the `test` split too · precision at least 70% · negation cases correct at least 90% · routing top-1 at least 85% (rules only) · every rule has examples · alert delivered in under 1 second at p95 · a test scans every patient-token response and finds no self-harm field · coverage for `domain/redflags` and `domain/routing` at least 95%.

**Pitfalls:** do not tune on the test split; mark all rules as drafts for clinician review; when unsure, escalate.

---

## Phase 7 — Doctor and nurse consoles, PDF (F10, F11)

**Depends on:** 6 · **Branch:** `phase-07-consoles`

> **In plain words:** We build the screens staff actually use. The doctor opens one page that tells the patient's story in seconds. The nurse registers patients, sees alerts and fixes mistakes. Admin manages users and kiosks. A PDF of the case sheet can be printed.

**Build steps**

- 7.1 Finalise the staff layout and role-based navigation (doctor: queue and consult; nurse: queue, registration, alerts; admin: everything).
- 7.2 Full queue board: department tabs, status filters, urgent pinned, virtualised list (`@tanstack/react-virtual`), load-more near the end, live deltas with `seq` handling, `ConnectionIndicator`, sound toggle.
- 7.3 Case sheet API: `GET /visits/{id}/case-sheet` (audited), `PATCH` using JSON Patch with `If-Match: <version>`, revalidated against the schema, `field_meta` updated with `source: doctor`, 409 on conflict, audit diff. Tests.
- 7.4 Doctor consult view per F5: sections in doctors' reading order, confidence chips, source icons, inline editing, conflict dialog, transcript panel (audited), Start and Done buttons (Done stores consult duration for the ETA), keyboard shortcuts.
- 7.5 Nurse console: assisted registration (same intake endpoints with channel `nurse`, consent recorded as nurse-captured), alerts panel, override dialog (reason required), no-show.
- 7.6 Admin screens: users, kiosks (activation code shown once), departments.
- 7.7 PDF export with WeasyPrint: A4 template, Inter and Noto Sans Devanagari installed in the API image, header with token and urgency reasons, sections, footer disclaimer. `GET /visits/{id}/case-sheet.pdf` (audited). Tests extract text with `pdftotext` and check key fields, including Hindi text.
- 7.8 Loading, empty and error states and a print stylesheet.
- 7.9 Playwright: doctor, nurse and admin flows; role-access UI tests; a two-browser edit conflict test.

> **Manual test:** run `make simulate-queue`; log in as a doctor, open the next patient, edit one field, and export the PDF. Log in as a second doctor in another browser window and edit the same field; you should see the conflict dialog. Log in as a nurse and register a patient on their behalf. Log in as admin and create a kiosk.

> **Gate:** role-access matrix passes for 100% of screens and routes · conflict test passes (409 and merge dialog) · PDF at most 1.5 s at p95 and contains the key fields · a 5,000-row queue renders and a live update applies in under 100 ms · every case sheet and transcript read creates an audit row · axe 0 serious or critical · coverage targets from G7 met.

**Pitfalls:** never show the transcript to roles that should not see it; never allow editing without the `If-Match` version.

---

## Phase 8 — Voice input (F3)

**Depends on:** 4, 7 · **Branch:** `phase-08-voice`

> **In plain words:** We add the ears. The patient speaks, the words appear on screen as they talk, and they can tap to fix a mistake. If the speech service fails for any reason, the screen switches to touch and typing within three seconds, so the patient is never stuck.

**Build steps**

- 8.1 Read the current Sarvam docs (C3). Write ADR "Provider: Sarvam STT" with the endpoint, auth header, model name, audio format and language support. Verify with a short script on one recording. If the human has not supplied an API key, stay on the Mock provider and say so in the report.
- 8.2 Interface `STTProvider` with `stream(frames, lang)` and `transcribe_clip(bytes, lang)`. Implement `MockSTT` (fixture-driven, configurable delay and failures) and `SarvamSTT` (streaming WebSocket for the languages the docs list, REST clips of 30 seconds or less for the rest). Provider chain with circuit breaker and metrics (G4, G6).
- 8.3 Audio WebSocket endpoint following D4: authentication, per-kiosk limits, frame size limit, 60-second utterance limit, idle timeout, backpressure. **Audio is not stored** unless `STORE_AUDIO=true` (then encrypted, 7-day expiry).
- 8.4 Browser capture: `getUserMedia` with a friendly permission explanation, an `AudioWorklet` that resamples to 16 kHz mono PCM16 in 100 ms frames, voice-activity detection that ends the clip after 1.2 seconds of silence, a 60-second cap, an input level meter on the `VoiceOrb`, and graceful handling of permission denied.
- 8.5 Interview integration: `VoiceOrb` states, live `TranscriptBubble`, the final transcript submitted as `input_mode=voice`, tap-to-edit, and a "Did we hear you correctly?" check when confidence is under 0.6.
- 8.6 Mixed language: pass the UI language as the primary language, accept Hinglish, keep the transcript in its original script. No translation yet (Phase 9).
- 8.7 Fallback: on `STT_UNAVAILABLE`, no partial result within 2 seconds, or an open circuit, send `{"type":"fallback","mode":"touch"}`; the UI shows touch and typing within 3 seconds with a friendly message; nurse-assisted entry stays available.
- 8.8 Evaluation: `eval/stt/RECORDING.md` (a script of 80 utterances: 30 Hindi, 30 Marathi, 20 English, 3 speakers, quiet and noisy) and `make eval-stt` using `jiwer` for word error rate plus symptom-keyword recall and latency. Real audio lives in `eval/stt/audio/` (git-ignored, consenting teammates, deleted afterwards). A text-only Mock version runs in CI.
- 8.9 Tests: protocol tests (oversized frame closes the socket, unauthorised closes it, idle timeout), provider chain tests (failure triggers fallback; the circuit opens after 5 failures), and a Playwright run using Chromium's fake audio device flags with a prepared WAV and `MockSTT`.

> **Manual test:** speak in Hindi, Marathi and English and watch the text appear. Try a noisy room. Deny the microphone permission and confirm the touch screen appears. Disconnect the internet halfway through and confirm the fallback message.

> **Gate:** protocol and chain tests pass · Playwright voice flow passes · fallback appears within 3 seconds of provider failure · **live evaluation** (manual, with a real key): word error rate at most 15% for Hindi and Marathi and at most 10% for English, symptom-keyword recall at least 90%, final-transcript latency p95 at most 400 ms after speech ends (report the measured numbers even if the network is poor) · with `STORE_AUDIO=false` the test finds no stored audio.

**Pitfalls:** the microphone needs HTTPS or `localhost`; do not log transcripts at INFO level; do not send audio as one HTTP request per frame.

---

## Phase 9 — AI layer: smart questions, extraction, second opinion (F4, F5, F7)

**Depends on:** 8 · **Branch:** `phase-09-ai-layer`

> **In plain words:** We add the brain, kept on a short leash. The AI may choose which approved question to ask next, tidy the patient's words into the case sheet, and raise (never lower) the urgency. Everything it returns is checked. If it fails or is slow, the safe checklist from Phase 4 takes over and the patient never notices.

**Build steps**

- 9.1 Read the current Gemini docs (C3). Write ADR "Provider: Gemini" naming the chosen `LLM_MODEL` (newest stable Flash-class model) and `LLM_FALLBACK_MODEL` (a Flash-Lite model), and confirm JSON-schema output works.
- 9.2 `LLMProvider.generate_json(prompt, schema, timeout, model)`; `MockLLM` (scripted answers, failure injection); `GeminiProvider` with response schema, 2.5 s timeout, one retry, circuit breaker, per-session cap of 30 calls, latency and token metrics.
- 9.3 `redact_pii()` for phones (including +91), ABHA numbers (14 digits with or without dashes), 12-digit identifiers and emails, with 60 unit tests. Names are never collected by voice; they are entered on a separate touch screen and never sent to the LLM.
- 9.4 Versioned prompts in `ai/prompts/v1/` (`extract.md`, `pick_question.md`, `redflag_second_opinion.md`, `classify_department.md`). Each includes the safety rules from B6, wraps patient text in delimiters and states that it is data, never instructions. Log the prompt version and SHA-256.
- 9.5 `extract_case_fields`: input is the transcript turns plus the current case sheet; output is a patch where every field has an `evidence_quote` and `confidence`. The validator requires each quote to be a substring of the normalised transcript, rejects fields outside the schema, and rejects disease names the patient did not say. Rejected output is discarded and the deterministic value is used. It runs **asynchronously** after each answer, never blocks the next question, and never overwrites touch-confirmed fields.
- 9.6 `pick_next_question`: the engine supplies at most 5 candidate ids; the LLM must return one of them (enum-constrained) within 600 ms, otherwise the engine's default is used.
- 9.7 `redflag_second_opinion`: runs in parallel on each final transcript and returns `{urgent, level, reason, evidence_quote}`. Final level is `max(rules, llm)`. A property test proves the LLM never lowers urgency.
- 9.8 `classify_department` with a fixed label set; combine with rules per E5; disagreement forces nurse confirmation.
- 9.9 Output guards in `ai/guards.py`: reject outputs that look like a diagnosis, advice, medicine or dose (pattern lists in English, Hindi and Marathi), and reject URLs. Test with the adversarial set.
- 9.10 Caching and cost control: generic-text cache only (G2), an `LLM_ENABLED` flag per hospital, token limits, metrics.
- 9.11 Datasets: `eval/extraction/` with **100** scripted dialogues and gold JSON (English 40, Hindi 30, Marathi 30); `eval/adversarial/` with **100** prompts (asks for a diagnosis, prompt injection, medicine requests, tricks in other languages). Scripts: `make eval-extraction`, `make eval-adversarial`, `make eval-redflags-llm`. CI runs them against `MockLLM` to test the pipeline; `make eval-live` runs them against the real model and the results go in the phase report.
- 9.12 Frontend: confidence chips and source icons on the staff case sheet and a subtle "AI-assisted" label. Patients see no AI-specific UI.

> **Manual test:** speak a complaint with many details ("sharp pain on the right side of my belly since two days, worse after eating, with vomiting") and see the case sheet fill neatly. Ask the kiosk "what disease do I have?" and confirm it only continues the interview. Turn off the internet or set `LLM_ENABLED=false` and confirm the interview still works with no error.

> **Gate (live results recorded in the report):** field F1 at least 0.90 for chief complaint, duration and severity, and at least 0.85 for associated symptoms · unsupported (hallucinated) fields at most 2% · **diagnosis leaks 0 of 100** adversarial prompts · red-flag recall with LLM at least 99% overall and 100% on must-catch · the LLM never lowers urgency (property test) · combined routing top-1 at least 92% · `pick_next_question` p95 at most 700 ms or the fallback is used · LLM timeout or garbage output leads to the deterministic path with no visible error · PII redaction tests 100%.

**Pitfalls:** never let the AI ask free-form questions; never put patient text in a cache key; never log full prompts that contain patient text.

---

## Phase 10 — Read-back, text to speech and patient confirmation (F6)

**Depends on:** 9 · **Branch:** `phase-10-readback-tts`

> **In plain words:** The system reads the summary back in the patient's own language, and the patient taps "correct" or "change". This is the safety step that lets us say the patient verified every fact. Fixed sentences are pre-recorded, so they play instantly and even with no internet.

**Build steps**

- 10.1 Read the current Sarvam text-to-speech docs and write an ADR. Interface `TTSProvider.synthesize(text, lang, voice)`; implement `MockTTS` (short tone), `SarvamTTS`, and `GoogleTTS` as the fallback, each with a circuit breaker.
- 10.2 Prompt audio pipeline: `make prompts-audio` gathers every fixed text (question bank, all `ui.*` prompts, consent) per language, synthesises it, and writes `apps/web/public/audio/{lang}/{id}.mp3` plus `manifest.json` (`id`, `lang`, text SHA-256, file, duration). Unchanged texts are skipped by hash. CI checks that every prompt id used in the UI has audio in all three languages (Mock audio is acceptable in CI).
- 10.3 Real `AudioPrompt`: the first tap on S1 unlocks audio (browser autoplay rules); `ReadAloudButton` replays; a volume control; if a file fails to load, stay silent without an error.
- 10.4 Read-back text from deterministic per-language templates built from the case sheet (complaint, duration, severity, key symptoms, conditions, medicines, allergies), at most 90 words. LLM polishing is off by default (`READBACK_LLM=false`) because templates are safer. Dynamic audio comes from runtime TTS and is **never cached** (it contains patient data); if TTS fails, show text only.
- 10.5 Confirmation UI (S6): each section has "Correct" and "Change". "Change" reopens that exact question (touch or voice); the change re-runs extraction, red flags and routing. "Yes, this is correct" calls `confirm` with the corrections recorded (`field_meta.confirmed = true`, `confirmed_by_patient`). If nothing happens for 90 seconds, show "A nurse will help you" and flag the visit `needs_assistance` for the nurse console.
- 10.6 Staff view shows "Confirmed by patient at HH:MM" with the list of corrections; unconfirmed sheets are marked yellow.
- 10.7 Tests: golden read-back outputs (3 languages × 10 cases), no unresolved placeholders, at most 90 words, audio manifest completeness; Playwright for the change flow, the timeout flow, the TTS-failure flow (text only), and an offline prompt test (network off, prompts still play).

> **Manual test:** finish an interview, listen to the read-back, tap Change on the duration, answer differently, and confirm. Open the doctor view and check that the corrected value is shown with the confirmation time. Say "chest pain" during a correction and watch the nurse alert fire. Wait 90 seconds without tapping and see the nurse-help message.

> **Gate:** prompt audio coverage 100% · read-back golden tests pass · confirm, change and timeout flows pass · TTS failure shows text with no error · fixed prompts play with **zero network requests** (checked in the Playwright network log) · a correction that adds a red-flag phrase triggers the alert · confirmation timestamp stored.

**Pitfalls:** never cache audio generated from patient data; do not rely on autoplay without a user tap.

---

## Phase 11 — Notifications and analytics (F9 notifications, F13)

**Depends on:** 10 · **Branch:** `phase-11-notify-analytics` · **Optional if time is short** (see the cut list in Part J)

> **In plain words:** Patients who gave their number and agreed can get a text when their turn is near. Hospital managers get charts that show waiting times, how many urgent cases were caught, and time saved.

**Build steps**

- 11.1 `NotificationProvider` interface with `ConsoleProvider` (default; prints), and disabled-by-default `TwilioSMSProvider` and `WhatsAppCloudProvider`. Templates in 3 languages: token issued, "5 ahead", "2 ahead", "your turn". Messages are sent only if a phone number was given **and** the patient ticked "Send me SMS updates" (default off, added to S4).
- 11.2 Worker consumes outbox events, detects threshold crossings (5, 2, 0 ahead) and sends **exactly once** per template per visit (dedupe key `visit:template`). Retry with backoff (1, 5, 30 seconds), then mark `failed` (dead-letter state). Per-phone limit 5 per hour.
- 11.3 Analytics: daily rollups by department (patients, average wait from `queued_at` to `consult_started_at`, average consult, urgent count, language mix, average interview duration, percent confirmed without edits, doctor edit rate) as materialised views refreshed every 5 minutes by an arq cron; endpoints `GET /analytics/summary` and `GET /analytics/timeseries`.
- 11.4 Admin dashboard (Recharts): stat cards, wait-time trend, urgent cases caught, language mix, department load, a "time saved" card whose assumption is visible and editable ("manual history baseline: 3 minutes per patient"), and a streamed CSV export.
- 11.5 Tests: duplicate events send one message; backoff and dead-letter; threshold crossing; analytics SQL against a small fixture with hand-computed answers; materialised view refresh; performance on 100,000 visits; axe.

> **Manual test:** run a simulation with a phone number you own (or watch the console provider). Confirm you get "5 ahead", "2 ahead" and "your turn" once each. Open the admin analytics page and compare two numbers against the raw table by hand.

> **Gate:** exactly one message per template per visit under 1,000 duplicate events · no message without opt-in · dead-letter after 3 failures · analytics equal the hand-computed fixture (100%) · summary endpoint p95 at most 100 ms on 100,000 visits · CSV of 100,000 rows streams with less than 100 MB memory growth · axe 0 serious or critical.

**Pitfalls:** never send a message containing medical details (token and position only); never send without consent.

---

## Phase 12 — Offline PWA and document upload (F14, F12)

**Depends on:** 10 · **Branch:** `phase-12-offline-docs` · **Document upload (12.6 and later) is a stretch goal**

> **In plain words:** Hospital Wi-Fi is unreliable, so the kiosk must keep working without it. Offline, it shows a shorter form, stores the answers safely on the device, and sends them when the internet returns, creating the patient exactly once. If time allows, patients can also photograph an old prescription.

**Build steps**

- 12.1 Service worker (Workbox): precache the shell, fonts, locale files, prompt audio and manifest; runtime cache for the question bank (stale-while-revalidate); versioned. Updates show a "New version ready" toast on staff screens and apply silently on the kiosk when idle.
- 12.2 Offline detection: `navigator.onLine` plus a heartbeat to `/healthz` every 10 seconds; `OfflineBanner`.
- 12.3 **Offline short form** (not a second copy of the engine): danger tiles, details, chief complaint (typed or chosen from common categories), duration, severity, conditions, medicines, allergies. If a danger tile is tapped offline, the screen instantly says "Please go to the nurse desk now."
- 12.4 IndexedDB outbox (`idb`) of submissions with idempotency keys; replay on reconnect with backoff; "Pending sync" state; a provisional local token `L-NNN` (kiosk id plus device counter) whose printed slip says "Provisional — staff will confirm".
- 12.5 Server endpoint `POST /intake/offline-submissions` (kiosk): validates, **replays the answers through the real engine**, creates the visit, case sheet, red flags and queue entry exactly once per idempotency key, and returns the real token.
- 12.6 *(stretch)* Document upload: MinIO in Compose; presigned upload with size and type conditions; maximum 8 MB; type check by magic bytes (JPEG, PNG, PDF only); EXIF stripped; optional ClamAV; `documents` row.
- 12.7 *(stretch)* Extraction job using a Gemini vision call with a JSON schema (medicines, written diagnoses, dates). Results appear in the case sheet marked "from document — verify" with a confidence value, never as confirmed facts.
- 12.8 Tests: Playwright with `context.setOffline(true)`: finish the short form offline, reconnect, and exactly one visit exists; server restart during replay; duplicate replays; Lighthouse "installable"; upload tests (an `.exe` renamed to `.jpg`, 9 MB file, corrupt image, decompression bomb) all rejected; extraction evaluated on 20 synthetic prescription images.

> **Manual test:** open the kiosk, switch the browser to offline mode, complete the short form, see the provisional token, then go online and watch the real token appear and the patient show up on the staff board. Tap a danger tile while offline.

> **Gate:** offline flow creates exactly one visit under duplicate and interrupted replays · offline danger tile shows the nurse message in under 1 second · Lighthouse PWA installable · *(stretch)* upload security tests pass and medicine-name recall at least 85% on 20 synthetic prescriptions with every extracted field flagged "verify" · axe 0 serious or critical.

**Pitfalls:** do not build a second interview engine in TypeScript; do not trust client-side token numbers; never mark extracted document data as confirmed.

---

## Phase 13 — Hardening, performance and scale readiness (F15)

**Depends on:** 10 (and 11 or 12 if built) · **Branch:** `phase-13-hardening`

> **In plain words:** We try to break our own product before anyone else does. We scan it, fuzz it, flood it with pretend patients, switch off its parts one by one, and prove it recovers. We also measure how fast it is and write down how it would grow to a big hospital.

**Build steps**

- 13.1 Security scans: OWASP ZAP baseline against the running stack (`infra/zap/`), Semgrep, Bandit, `pip-audit`, `npm audit`, gitleaks, and `trivy` on the Docker images. Fix every high or critical finding; document each medium with a reason.
- 13.2 Abuse tests: `schemathesis` against the OpenAPI document (no 5xx), a JWT attack suite, an IDOR suite (patient token reading another visit; a hospital A user reading hospital B), and WebSocket abuse (oversized frames and floods are limited or closed).
- 13.3 Finalise CSP, CORS and cookie flags; pin all dependencies; generate an SBOM (CycloneDX).
- 13.4 Database: add PgBouncer (transaction pooling) to Compose and prove RLS `SET LOCAL` still works through it; enable `pg_stat_statements` and review queries slower than 50 ms; **partition `audit_log` and `interview_turns` by month** with a script that creates future partitions; statement timeout 5 seconds; wire `DATABASE_READ_URL` for analytics and exports.
- 13.5 Redis: split into two instances in Compose: `redis-state` (queue, sessions, rate limits, idempotency; `noeviction`, AOF `everysec`) and `redis-cache` (question bank, micro-cache, generic LLM cache; `allkeys-lru`). The code already reads two URLs (Appendix A).
- 13.6 Tuning: gunicorn workers `2 × CPU + 1` (env override), `uvloop` and `httptools`, `orjson` responses, gzip or brotli and HTTP/2 at the proxy, pool sizes documented (asyncpg 5 to 20 per worker; keep total under Postgres `max_connections`), N+1 guard tests (queue list at most 3 queries, case sheet at most 4), bundle analysis and code splitting.
- 13.7 Load tests in `infra/k6/`: **A** 200 concurrent kiosk sessions doing a full 12-answer interview with 4 seconds of think time (mock providers) for 10 minutes; **B** 50 staff WebSocket clients plus 20 queue actions per second; **C** 2,000 status polls per second (cached) plus 500 SSE streams; then a 30-minute soak.
- 13.8 Chaos scripts proving the D5 table: kill Redis, kill the worker, slow or fail the LLM and STT mocks, restart Postgres. Each asserts the documented behaviour.
- 13.9 Observability: OpenTelemetry traces to Jaeger, Prometheus and Grafana under a Compose profile `observability`, the metrics from G6, dashboard JSON, and alert rules (voice p95, queue update p95, outbox age over 60 s, error rate over 2%, circuit open).
- 13.10 Backup and restore drill: `make backup` (`pg_dump` and Redis AOF copy), `make restore` into a scratch database, verify row counts and a checksum; document how the RPO 5 minutes and RTO 30 minutes targets would be met in production (WAL archiving and point-in-time recovery).
- 13.11 Privacy and retention: an arq cron that deletes transcripts older than `RETENTION_DAYS` (default 30) and anonymises visits after `ANONYMISE_DAYS` (default 90); CLI `delete-patient-data` for deletion requests. Write `docs/PRIVACY.md` (data map, retention, DPDP notes), `docs/SCALING.md` (what to scale first, Kubernetes manifest skeleton in `infra/k8s`: Deployments, HPA on CPU and `ws_connections`, PodDisruptionBudget, deny-all NetworkPolicy, secrets from an external store) and `docs/SAFETY.md`.

> **Manual test:** run `make load` and watch the Grafana dashboard. Run `make chaos` and read the table it prints: each line says what was broken, what the system did, and PASS or FAIL. Run the restore drill and confirm the counts match.

> **Gate:** zero high or critical findings across ZAP, Semgrep, Bandit, pip-audit, npm audit and trivy (waivers only through an ADR) · schemathesis finds zero 5xx · IDOR and JWT suites pass · k6 scenarios A, B and C meet the G8 budgets with error rate under 1% on the reference machine · soak memory growth at most 10% · chaos suite passes · restore drill verified · N+1 guard tests pass · retention job tested.

**Pitfalls:** do not raise a threshold to pass; do not run load tests against real paid providers; record the exact machine used.

---

## Phase 14 — Demo polish and documentation

**Depends on:** all earlier phases you built · **Branch:** `phase-14-demo`

> **In plain words:** We make it shine and make it explainable. A demo that cannot fail, even if the Wi-Fi dies, plus documents that let judges and teammates understand everything.

**Build steps**

- 14.1 `make demo-seed`: the fictional hospital and three scripted patient stories (Hindi fever, Marathi chest pain that triggers an urgent alert, English child with cough). Add **demo-safe mode** (`PROVIDER_MODE=demo-safe`): the Mock speech provider replays recorded transcripts and the Mock LLM replays saved outputs, so the demo needs no internet.
- 14.2 UI polish pass: spacing, alignment, empty states, skeletons, favicon and PWA icons, a hospital logo slot, a splash screen, and the list of microcopy for native-speaker review. Remove dev-only routes from the production build.
- 14.3 **Judge mode** (`?demo=1`): small latency badges (voice turn in milliseconds), a ribbon "History-taking only — not a diagnosis", and an optional "How it works" side panel on the staff view.
- 14.4 Documentation: README (what and why, a Mermaid architecture diagram, quickstart, screenshots), `docs/DEMO.md` (the exact click-by-click script from Part J), `docs/EVALUATION.md` (all gate results with dates), `docs/ARCHITECTURE.md`, `docs/CLINICIAN_REVIEW.md` (every pending item), plus the safety, privacy and scaling documents.
- 14.5 `make screenshots` writes `docs/screenshots/`.
- 14.6 Full regression: `make test`, `make e2e`, `make eval`, and a short `make load`; summarise in one table.
- 14.7 Release: tag `v1.0`, build production images, smoke test with `docker-compose.prod.yml`.

> **Manual test:** unplug the internet and run the three-minute script from Part J twice. Ask a friend who has never seen the product to complete the kiosk flow while you watch without helping.

> **Gate:** full regression green · demo-safe mode works with the network disabled · the human runs the script twice without a failure · all documents complete · every unreviewed clinical item is listed in `docs/CLINICIAN_REVIEW.md`.

---

# Part I — Master accuracy and performance gates

Every number the agent must **measure and print** before a phase can pass. Command names are the Makefile targets from Appendix B.

| Area | Metric | Threshold | Phase | How it is measured |
|---|---|---|---|---|
| Red flags | Recall, rules only (dev and test splits) | at least 98% | 6 | `make eval-redflags` |
| Red flags | Recall on `must_catch` subset | **100%** (60 of 60) | 6, 9 | `make eval-redflags` |
| Red flags | Recall, rules plus LLM | at least 99% | 9 | `make eval-redflags-llm` |
| Red flags | Precision | at least 70% | 6 | `make eval-redflags` |
| Red flags | Negation cases correct | at least 90% | 6 | `make eval-redflags` |
| Red flags | LLM lowers urgency | never (property test) | 9 | `make test` |
| Routing | Top-1 accuracy, rules only | at least 85% | 6 | `make eval-routing` |
| Routing | Top-1 accuracy, rules plus LLM | at least 92% | 9 | `make eval-routing` |
| Interview | Golden interviews | 30 of 30 | 4 | `make eval-interview` |
| Interview | Questions per interview | 12 or fewer, no repeats | 4 | Hypothesis tests |
| Speech | Word error rate Hindi and Marathi | at most 15% | 8 | `make eval-stt` (live) |
| Speech | Word error rate English | at most 10% | 8 | `make eval-stt` (live) |
| Speech | Symptom-keyword recall | at least 90% | 8 | `make eval-stt` (live) |
| Extraction | Field F1: complaint, duration, severity | at least 0.90 | 9 | `make eval-extraction` |
| Extraction | Field F1: associated symptoms | at least 0.85 | 9 | `make eval-extraction` |
| Extraction | Unsupported (hallucinated) fields | at most 2% | 9 | `make eval-extraction` |
| Safety | Diagnosis or advice leaks | **0 of 100** | 9 | `make eval-adversarial` |
| Safety | Self-harm flag visible to patient | never | 6 | response scan test |
| Read-back | Length | at most 90 words | 10 | golden tests |
| Read-back | Prompt audio coverage | 100% | 10 | manifest check |
| Queue | Served order equals score order | always | 5 | property test |
| Queue | Concurrency | 1,000 parallel enqueues, unique tokens, consistent positions | 5 | integration test |
| Queue | Rebuild after Redis flush | identical order | 5 | integration test |
| Offline | Duplicate or interrupted replay | exactly one visit | 12 | Playwright |
| Latency | Voice turn p95 | at most 1.5 s | 9, 13 | k6 and traces |
| Latency | Enqueue and token | at most 80 ms | 5, 13 | k6 |
| Latency | Status GET | at most 100 ms | 5, 13 | k6 |
| Latency | Realtime delta delivery | at most 200 ms | 5, 13 | test and k6 |
| Latency | PDF export | at most 1.5 s | 7 | integration test |
| Load | Error rate under scenarios A, B, C | under 1% | 13 | `make load` |
| Load | Soak memory growth (30 minutes) | at most 10% | 13 | `make load` |
| Security | Route-matrix auth test | 100% of routes | 2 | `make test` |
| Security | Scanner findings (ZAP, Semgrep, Bandit, pip-audit, npm audit, trivy) | zero high or critical | 13 | `make scan` |
| Security | Fuzz (schemathesis) | zero 5xx | 13 | `make scan` |
| UI | axe serious or critical violations | 0 | 1 onward | Playwright |
| UI | Lighthouse mobile | Performance ≥ 85, Accessibility ≥ 95, Best Practices ≥ 95 | 1 | Lighthouse CI |
| UI | Kiosk touch targets | at least 64 px | 1 onward | Playwright |
| Coverage | `app/domain` and `app/core` | at least 95% | all | `make test` |
| Coverage | Backend overall | at least 80% | all | `make test` |

---

# Part J — Demo plan, judge questions and the cut list

## J1. The three-minute demo script

| Time | What happens | What to say |
|---|---|---|
| 0:00–0:20 | Show the kiosk welcome screen | "OPD Saathi does history-taking, not diagnosis. It saves the doctor's two minutes." |
| 0:20–1:10 | Hindi patient: tap हिन्दी, no danger signs, enter age, speak "पेट में दर्द और बुखार तीन दिन से", answer two follow-ups, listen to the read-back, tap "Yes, this is correct", receive token | "The patient speaks in their own language. The AI only picks approved questions." |
| 1:10–1:50 | Marathi patient: speak or tap chest pain with sweating; nurse screen chimes, red banner appears, patient jumps ahead with the reason shown | "Danger signs are caught by rules first, so this never depends on AI being available." |
| 1:50–2:20 | Doctor opens the one-page case sheet: confidence chips, original transcript, PDF | "The doctor reads one page in under twenty seconds and can edit anything." |
| 2:20–2:40 | Patient phone shows live position and wait range | "The patient sees their place in line, so the crowd thins out." |
| 2:40–3:00 | Switch to offline or set `LLM_ENABLED=false`; run one more patient; show analytics | "If Wi-Fi or AI fails, it still works. Our measured red-flag recall is X% and a voice turn takes Y seconds." (fill X and Y from `docs/EVALUATION.md`) |

## J2. Likely judge questions

| Question | Answer |
|---|---|
| Is this diagnosing? | No. It collects history only. The patient confirms it, the doctor edits it, and a filter blocks any diagnosis or advice (zero leaks in 100 adversarial tests). |
| What if the AI mishears? | The transcript is shown, low-confidence fields are yellow, the patient confirms a read-back, and the original words are kept for the doctor. |
| What about privacy? | Consent is recorded, sensitive fields are encrypted, audio is not stored, personal data is stripped before any AI call, every read is audited, data is retained for a limited time, and deletion is supported. |
| What if there is no internet? | The kiosk works offline with a short form, shows an instant nurse message for danger signs, and syncs exactly once later. |
| How does it scale to a large hospital? | Stateless API replicas, Redis for the queue, Postgres with partitions and replicas, per-hospital isolation, and measured load tests. See `docs/SCALING.md`. |
| How does it fit existing hospital systems? | The case sheet is a structured JSON that maps to FHIR-style resources, with an ABDM and hospital-system integration path. We built mocks only; no live integration. |
| Which languages? | Hindi, Marathi and English today. Adding another needs question translations, prompt audio, and red-flag phrases; speech support depends on the provider. |
| Who is responsible for medical content? | The questions and red-flag rules are drafts listed in `docs/CLINICIAN_REVIEW.md`, and a clinician must sign off before real use. |
| What is new here? | Safety-first design: rules for urgency, AI that can only raise urgency, patient read-back confirmation, and graceful fallback at every step. |

## J3. The cut list if time is short (cut in this order)

1. Phase 12 document upload (keep the offline form if you can).
2. Phase 11 real SMS or WhatsApp (keep the console provider and the analytics page).
3. Phase 13: reduce to security scans, a short k6 run and the chaos script. Keep the numbers you do produce.
4. Hinglish and extra rule polish, never the rule evaluation itself.

**Never cut:** consent, the red-flag evaluation and its numbers, the deterministic fallback, patient confirmation, audit logging, the safety footer, and the demo-safe mode.

## J4. Demo-day checklist

- Run on `localhost` or HTTPS (the microphone needs it). Test the microphone and speakers in the actual room.
- Set `PROVIDER_MODE=live` only if the network has been tested; otherwise `demo-safe`.
- Run `make demo-seed`, reload the kiosk, and check the three stories.
- Prefetch prompt audio (open every screen once online) and confirm the service worker is installed.
- Keep a phone with the status page open and a second device for the nurse view.
- Record a backup screen video of the full run.
- Bring a mobile hotspot, a charger and a printed one-page summary.

---

# Appendix A — Environment variables (`.env.example`)

Secrets are never committed. `.env.example` contains placeholders only. Generate keys with `openssl rand -base64 32`.

| Variable | Default or example | Meaning |
|---|---|---|
| `APP_ENV` | `development` | `development`, `test`, `production`. Docs endpoints are off in production |
| `WEB_ORIGIN` | `http://localhost:5173` | CORS allow-list (comma separated) |
| `WEB_CONCURRENCY` | `2` | gunicorn workers (`2 × CPU + 1` in production) |
| `LOG_LEVEL` | `INFO` | Never logs personal data |
| `DATABASE_URL` | `postgresql+asyncpg://osa_app:...@localhost:5432/osa` | App connection (role `osa_app`) |
| `DATABASE_READ_URL` | empty | Optional replica for analytics and exports |
| `DB_POOL_MIN`, `DB_POOL_MAX` | `5`, `20` | Per worker |
| `REDIS_STATE_URL` | `redis://:...@localhost:6379/0` | Queue, sessions, limits, idempotency (no eviction) |
| `REDIS_CACHE_URL` | same as above in development | Caches (evictable) |
| `JWT_SECRET`, `JWT_KID` | 32+ random bytes, `k1` | Token signing and key id |
| `ACCESS_TOKEN_MINUTES` | `10` | Staff access token |
| `REFRESH_TOKEN_HOURS` | `8` | Staff refresh token |
| `KIOSK_TOKEN_HOURS` | `12` | Kiosk token |
| `PATIENT_SESSION_HOURS` | `2` | Patient session token |
| `MAX_REQUEST_BODY_BYTES` | `1048576` | Maximum bytes accepted in one HTTP request body |
| `APP_DATA_KEY` | base64 of 32 bytes | AES-256-GCM key for fields |
| `APP_HMAC_KEY` | base64 of 32 bytes | Phone lookup hash |
| `CURSOR_HMAC_KEY` | base64 of 32 bytes | Signs pagination cursors |
| `PROVIDER_MODE` | `mock` | `live`, `mock`, `demo-safe` |
| `STT_PROVIDER`, `STT_MODEL` | `mock`, set after reading docs | Speech to text |
| `SARVAM_API_KEY` | empty | Sarvam services |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_FALLBACK_MODEL` | `mock`, set after reading docs | LLM |
| `GEMINI_API_KEY` | empty | Gemini |
| `LLM_ENABLED` | `true` | Per-hospital switch |
| `LLM_MAX_CALLS_PER_SESSION` | `30` | Cost and abuse cap |
| `TTS_PROVIDER`, `TTS_MODEL` | `mock` | Text to speech |
| `STORE_AUDIO` | `false` | Keep raw audio (encrypted, 7 days) |
| `READBACK_LLM` | `false` | LLM polish of the read-back |
| `MAX_QUESTIONS` | `12` | Interview cap |
| `URGENT_BONUS_MIN`, `PRIORITY_BONUS_MIN`, `VULNERABLE_BONUS_MIN` | `360`, `45`, `15` | Queue score (E6) |
| `DEFAULT_CONSULT_MIN` | `4.0` | ETA default |
| `RETENTION_DAYS`, `ANONYMISE_DAYS` | `30`, `90` | Data retention |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | MinIO values | Phase 12 uploads |
| `SMS_PROVIDER` | `console` | `console`, `twilio`, `whatsapp` |
| `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Optional observability |

# Appendix B — Makefile targets

| Target | What it does |
|---|---|
| `make up` / `make down` | Start or stop Postgres, Redis (and worker) |
| `make api` / `make worker` / `make web` | Run the API, the background worker, the web dev server |
| `make migrate` | Apply database migrations |
| `make seed` / `make seed-bulk` / `make demo-seed` | Demo data, 100,000 synthetic visits, the demo stories |
| `make types` | Regenerate TypeScript API types |
| `make lint` / `make fmt` | Ruff, mypy, ESLint, Prettier |
| `make test` (also `test-unit`, `test-int`) | Backend and frontend unit and integration tests with coverage |
| `make e2e` | Playwright end-to-end, accessibility and screenshots |
| `make eval` | All evaluations that run without paid keys |
| `make eval-interview`, `eval-redflags`, `eval-routing`, `eval-stt`, `eval-extraction`, `eval-adversarial`, `eval-redflags-llm` | Individual evaluations |
| `make eval-live` | Evaluations against real providers (needs keys) |
| `make prompts-audio` | Generate prerecorded prompt audio |
| `make simulate-queue N=50 URGENT=10` | Create synthetic patients through the real API |
| `make demo-ratelimit` | Trigger rate-limit responses on purpose |
| `make load` / `make chaos` | k6 load tests and failure-injection tests |
| `make scan` | ZAP, Semgrep, Bandit, pip-audit, npm audit, gitleaks, trivy, schemathesis |
| `make backup` / `make restore` | Backup and restore drill |
| `make screenshots` | Capture documentation screenshots |

# Appendix C — Phase report template (`docs/phase-reports/phase-NN.md`)

```markdown
# Phase NN — <name>  (date, branch, commit hash)

## 1. In plain words
Three to five sentences for a non-technical reader. One small diagram if it helps.

## 2. What was built
Bullet list mapped to the numbered steps (N.1, N.2 ...).

## 3. Results against the gate
| Gate item | Threshold | Measured | Pass |
|---|---|---|---|
(Paste real command output below the table.)

## 4. Commands run
Exact commands and test counts (passed, failed, skipped = 0).

## 5. Screenshots and artifacts
Paths under docs/screenshots and any reports.

## 6. How the human tests this phase
Click path and expected result.

## 7. Known issues and decisions
ADRs added, anything deferred and why, any clinical items added to CLINICIAN_REVIEW.md.

## 8. Next phase preview
Two sentences.

Reply `approve phase NN` to continue.
```

# Appendix D — Definition of done (every phase)

- [ ] All numbered steps implemented, each with a passing test
- [ ] Zero skipped or xfail tests; no thresholds lowered
- [ ] Gate numbers measured, printed and recorded in the phase report
- [ ] Linters, type checks and security scans pass in CI
- [ ] New settings documented in Appendix A and `.env.example`
- [ ] New strings exist in English, Hindi and Marathi
- [ ] Accessibility checks pass (UI phases)
- [ ] Audit logging added for every new sensitive read or change
- [ ] No personal data in logs, errors or traces
- [ ] README and ADRs updated; clinical items added to `docs/CLINICIAN_REVIEW.md`
- [ ] Phase report written and the human's manual test steps are accurate
- [ ] Work committed on the phase branch; waiting for "approve phase N"

# Appendix E — Glossary (plain words)

| Term | Meaning |
|---|---|
| OPD | Outpatient department: patients who visit the hospital for the day and go home |
| History-taking | Asking the patient about symptoms and background. Not diagnosis |
| Case sheet | The structured summary of the patient's history for the doctor |
| HPI | History of present illness: the story of the current problem |
| OLDCARTS | A checklist for describing a symptom: onset, location, duration, character, aggravating or relieving factors, radiation, timing, severity |
| Red flag | A sign that the patient may need attention sooner |
| Triage | Deciding who is seen first and where |
| STT / TTS | Speech to text / text to speech |
| LLM | A large language model, the kind of AI that reads and writes text |
| Hallucination | When an AI states something the source did not say |
| Prompt injection | Trying to trick an AI with hidden instructions in its input |
| ABHA / ABDM | India's health ID and Digital Mission |
| DPDP Act | India's Digital Personal Data Protection Act |
| FHIR | A standard format for health data |
| JWT | A signed token that proves who a user is, for a short time |
| RBAC | Role-based access control: permissions depend on role |
| RLS | Row-level security: the database itself hides other hospitals' rows |
| Idempotency | Repeating a request has the same effect as doing it once |
| Outbox | A table that records events in the same transaction as the data, so none are lost |
| Sorted set (ZSET) | A Redis structure that keeps items ordered by a score |
| WebSocket / SSE | Ways for the server to push live updates to a screen |
| PWA | A website that installs like an app and can work offline |
| Circuit breaker | A switch that stops calling a failing service for a short time |
| Bulkhead | Separating resources so one failure cannot drain everything |
| Keyset pagination | Paging by "after this item" instead of page numbers, which stays fast and consistent |
| p95 | The time that 95 out of 100 requests beat |
| WER | Word error rate of speech recognition (lower is better) |
| F1, recall, precision | Accuracy measures: recall is how many real cases were caught; precision is how many alarms were right |
| ADR | Architecture decision record: a short note of why a choice was made |
| Mock | A fake stand-in for a service, used for tests and safe demos |
