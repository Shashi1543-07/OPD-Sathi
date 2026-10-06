# OPD Saathi

OPD Saathi is a multilingual, voice-first patient history-taking and queue system for crowded outpatient departments. It collects a patient's history for clinical staff; it does not diagnose or recommend treatment.

## Project status

Phases 0 through 10 are implemented locally on their phase branches. Phase 7 includes staff navigation, queue and consultation screens, nurse-assisted registration, hospital administration, audited multilingual case-sheet PDF, and loading, empty, error, and print states. PR #5 passed backend, frontend, and security and merged to `main` as `16d4952`. Phase 8 adds safe STT adapters, an authenticated audio socket, browser microphone capture, live transcript editing, mixed-script preservation, a three-second touch/typing fallback, and an 80-prompt consent-based evaluation plan. Mock remains the default, live-provider failure offers touch/typing, and audio is not persisted. Phase 9 adds constrained LLM question selection, evidence-checked case-sheet extraction, monotonic red-flag review, fixed-label department classification, unsafe-output guards, tenant controls, and provenance shown to staff; synthetic Mock gates pass, while real Gemini quality remains unmeasured. Phase 10 adds multilingual deterministic read-back, prompt audio precached for offline use, exact-question correction, patient confirmation, and staff visibility of confirmation and assistance status. Its API, web, and production browser checks pass; live TTS voice quality remains unmeasured. See the [Phase 9 report](docs/phase-reports/phase-09.md) and [Phase 10 report](docs/phase-reports/phase-10.md). Phase 6 clinical and Hindi/Marathi native-speaker review remains pending, as does Phase 4 question and translation review before real-world use. See the [ADR 003](docs/DECISIONS.md#adr-003--sarvam-real-time-speech-recognition), [Phase 8 report](docs/phase-reports/phase-08.md), [Phase 6 report](docs/phase-reports/phase-06.md), [Phase 7 report](docs/phase-reports/phase-07.md), [project checklist](OPD_Saathi_TODO.md), and [build playbook](OPD_Saathi_Agent_Playbook.md).

## Development setup

The supported local setup is Linux, macOS, or Windows with WSL2 and Docker. On Windows, use a WSL2 Linux distribution with Docker Desktop's WSL integration. Required runtimes are Python 3.12 and Node.js 24 LTS.

Environment check on 5 October 2026: Windows host; Ubuntu is installed as a WSL 2 distribution. From Ubuntu, Docker Desktop 4.93.0 (Engine 29.8.1, Compose 5.5.1), Python 3.12.15, Node.js 24.21.0, npm 11.19.0, and GNU Make are verified. Phase 0 CI is green on commit `462808d`; Compose, migrations, API readiness, tests, and the documented clean-checkout setup were also verified. A local development `.env` with random credentials has been created and verified ignored by Git.

1. Copy `.env.example` to `.env` and replace every local placeholder with private development values. Never commit `.env`.
2. Create and activate a Python 3.12 virtual environment, then install the backend lock: `python -m venv .venv`; activate it; `pip install --require-hashes -r services/api/requirements.lock.txt`.
3. Install the web lock with `npm ci --prefix apps/web`.
4. Start Postgres and Redis with `make up`.
5. Apply the schema with `make migrate`.
6. Run the API with `make api` and the web app with `make web` in separate terminals.
7. Visit `http://localhost:8000/docs`, `http://localhost:8000/readyz`, and `http://localhost:5173`.

The Phase 0 setup steps were run against a clean checkout of `phase-00-foundations`; see the [Phase 0 report](docs/phase-reports/phase-00.md) for the measured scope and results.

### Phase 2 staff and kiosk bootstrap

After applying migrations, run `python -m app.cli create-admin`. The operator prompt creates a synthetic hospital and its first administrator; it prints the hospital ID needed by the `/staff` sign-in form. To add a kiosk, run `python -m app.cli create-kiosk --hospital-id <hospital-id> --label "Demo kiosk"` and use the printed one-time code once with `POST /kiosks/activate`. The app stores only digests of refresh and activation secrets. The signed-in administrator can inspect the append-only audit feed at `GET /audit`; `make demo-ratelimit` sends 11 synthetic failed login requests to demonstrate the 429 response.

### Phase 3 synthetic demo data

Set persistent, private `APP_DATA_KEY` and `APP_HMAC_KEY` values (base64-encoded 32-byte keys), plus `DEMO_ADMIN_PASSWORD` and `DEMO_STAFF_PASSWORD` (at least 12 characters) in the ignored `.env` before starting the API. Keep the same encryption keys across restarts; changing them makes existing encrypted patient fields unreadable. After `make migrate`, run `make seed` to provision the fictional Nagpur hospital, its departments, staff, and kiosks. The CLI prints newly created administrator/staff credentials and kiosk activation codes once; store those securely. Run `make seed-bulk` to add 100,000 reproducible synthetic visits. Never use real patient information in this demo.

## Safety and data

Use synthetic data only. This software is for history-taking, not diagnosis. Clinical questions and safety rules remain drafts until reviewed by a clinician. No live hospital, ABHA, or ABDM integration is included in this MVP.
