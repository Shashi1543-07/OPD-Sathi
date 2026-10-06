# OPD Saathi

OPD Saathi is a multilingual, voice-first patient history-taking and queue system for crowded outpatient departments. It collects a patient's history for clinical staff; it does not diagnose or recommend treatment.

## Project status

Phases 0 through 6 are implemented locally. Phase 7 is active on `phase-07-consoles`; steps 7.1 through 7.6 now include role-filtered staff navigation, queue and consultation screens, nurse-assisted registration, and hospital administration for users, kiosks, and departments. Steps 7.7 through 7.9 and the Phase 7 acceptance gate remain in progress. Phase 6 adds draft red-flag rules, department routing, and department-scoped staff alerts; its local backend, evaluation, lint, type, and frontend gates pass. Phase 6 clinical and Hindi/Marathi native-speaker review remains pending, and its branch has not been pushed or merged. Phase 4 questions and translations also remain pending review before any real-world use. See the [Phase 6 report](docs/phase-reports/phase-06.md), [Phase 7 plan](docs/phase-plans/phase-07.md), the [Phase 5 report](docs/phase-reports/phase-05.md), [project checklist](OPD_Saathi_TODO.md), and [build playbook](AGENT_PLAYBOOK.md).

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
