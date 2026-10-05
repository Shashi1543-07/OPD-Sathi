# OPD Saathi

OPD Saathi is a multilingual, voice-first patient history-taking and queue system for crowded outpatient departments. It collects a patient's history for clinical staff; it does not diagnose or recommend treatment.

## Project status

Phase 0 — Foundations and tooling is awaiting confirmation of the GitHub Actions run for commit `b5cd827`. See [the phase checklist](OPD_Saathi_TODO.md), [the Phase 0 report](docs/phase-reports/phase-00.md), and [the build playbook](AGENT_PLAYBOOK.md). The source playbook was provided as `OPD_Saathi_Agent_Playbook.md` and is copied to the canonical name required by the repository layout.

## Development setup

The supported local setup is Linux, macOS, or Windows with WSL2 and Docker. On Windows, use a WSL2 Linux distribution with Docker Desktop's WSL integration. Required runtimes are Python 3.12 and Node.js 24 LTS.

Environment check on 5 October 2026: Windows host; Ubuntu is installed as a WSL 2 distribution. From Ubuntu, Docker Desktop 4.93.0 (Engine 29.8.1, Compose 5.5.1), Python 3.12.15, Node.js 24.21.0, npm 11.19.0, and GNU Make are verified. Compose services, migrations, API readiness, and the `make test` target passed. The fresh-checkout setup took 31 seconds with the toolchain installed and dependency/image caches warm. A local development `.env` with random credentials has been created and verified ignored by Git. The remaining Phase 0 gate is confirmation of green GitHub Actions for `b5cd827`.

1. Copy `.env.example` to `.env` and replace every local placeholder with private development values. Never commit `.env`.
2. Create and activate a Python 3.12 virtual environment, then install the backend lock: `python -m venv .venv`; activate it; `pip install --require-hashes -r services/api/requirements.lock.txt`.
3. Install the web lock with `npm ci --prefix apps/web`.
4. Start Postgres and Redis with `make up`.
5. Apply the schema with `make migrate`.
6. Run the API with `make api` and the web app with `make web` in separate terminals.
7. Visit `http://localhost:8000/docs`, `http://localhost:8000/readyz`, and `http://localhost:5173`.

The setup steps were run against a clean checkout of `phase-00-foundations`; see the Phase 0 report for the measured scope and results.

## Safety and data

Use synthetic data only. This software is for history-taking, not diagnosis. Clinical questions and safety rules remain drafts until reviewed by a clinician. No live hospital, ABHA, or ABDM integration is included in this MVP.
