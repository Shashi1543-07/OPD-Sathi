# OPD Saathi

OPD Saathi is a multilingual, voice-first patient history-taking and queue system for crowded outpatient departments. It collects a patient's history for clinical staff; it does not diagnose or recommend treatment.

## Project status

Phase 0, Phase 1, and Phase 2 are complete. Phase 2 was merged into `phase-01-ui-foundation` as `900d0ef`; all six GitHub checks passed. Phase 3 (data model, encryption, seeds, and pagination) is implemented on `phase-03-data-model`; local backend, PostgreSQL, frontend, and coverage gates pass. GitHub CI and merge are pending. See the [Phase 3 report](docs/phase-reports/phase-03.md), [Phase 3 plan](docs/phase-plans/phase-03.md), [project checklist](OPD_Saathi_TODO.md), [phase reports](docs/phase-reports/), and [build playbook](AGENT_PLAYBOOK.md). Phase 4 has not started and remains gated on approval.

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
