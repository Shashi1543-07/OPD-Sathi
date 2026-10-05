# OPD Saathi

OPD Saathi is a multilingual, voice-first patient history-taking and queue system for crowded outpatient departments. It collects a patient's history for clinical staff; it does not diagnose or recommend treatment.

## Project status

Phase 0 — Foundations and tooling is in progress. See [the phase checklist](OPD_Saathi_TODO.md) and [the build playbook](AGENT_PLAYBOOK.md). The source playbook was provided as `OPD_Saathi_Agent_Playbook.md` and is copied to the canonical name required by the repository layout.

## Development setup

The supported local setup is Linux, macOS, or Windows with WSL2 and Docker. On Windows, use a WSL2 Linux distribution with Docker Desktop's WSL integration. Required runtimes are Python 3.12 and Node.js 24 LTS.

Environment check on 5 October 2026: Windows host; Node.js 20.19.5 and npm 10.8.2 are globally available. The official Node.js 24.21.0 LTS Windows archive is also downloaded, SHA-256 verified, and unpacked under the ignored `.uvtooling` directory for this workspace. Python 3.12.15 was provisioned locally under `.uvtooling`; Docker is not installed or on PATH, and WSL2 reports access denied while enumerating distributions. Use Node.js 24 LTS for the project; set up Docker Desktop with WSL integration and an accessible WSL2 Linux distribution before attempting the full Compose gate.

1. Copy `.env.example` to `.env` and replace every local placeholder with private development values. Never commit `.env`.
2. Create and activate a Python 3.12 virtual environment, then install the backend lock: `python -m venv .venv`; activate it; `pip install --require-hashes -r services/api/requirements.lock.txt`.
3. Install the web lock with `npm ci --prefix apps/web`.
4. Start Postgres and Redis with `make up`.
5. Apply the schema with `make migrate`.
6. Run the API with `make api` and the web app with `make web` in separate terminals.
7. Visit `http://localhost:8000/docs`, `http://localhost:8000/readyz`, and `http://localhost:5173`.

The exact setup steps will be refined and verified against a fresh clone before Phase 0 passes.

## Safety and data

Use synthetic data only. This software is for history-taking, not diagnosis. Clinical questions and safety rules remain drafts until reviewed by a clinician. No live hospital, ABHA, or ABDM integration is included in this MVP.
