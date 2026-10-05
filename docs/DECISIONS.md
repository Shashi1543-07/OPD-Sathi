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
