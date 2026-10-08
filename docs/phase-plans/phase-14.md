# Phase 14 implementation plan

## Scope

Finish a repeatable synthetic demo, local-only provider mode, judge-facing cues, and current project documentation. Preserve clinical review as an explicit human gate.

## Checklist

- [x] Provide `make demo-seed` for the fictional Nagpur hospital.
- [x] Add `PROVIDER_MODE=demo-safe` that forces local STT/TTS adapters, saved schema-bounded LLM fixtures, and scripted Hindi fever, Marathi urgent chest pain, and English child cough transcripts.
- [x] Add `?demo=1` kiosk safety ribbon, measured voice-turn latency badge, and optional staff “How it works” panel.
- [x] Keep development-only routes excluded from production builds; configure favicon/PWA app icon.
- [x] Document quickstart, architecture, demo instructions, safety/privacy/scaling, and pending clinical review.
- [x] Add a repeatable `make screenshots` generator writing only to `docs/screenshots/phase-14/`.
- [x] Generate and inspect Phase 14 kiosk screenshots; generator writes only into the new `phase-14` directory.
- [x] Run frontend unit tests/build/lint/locales, focused API unit tests, and the deterministic synthetic evaluation aggregate.
- [x] Run the full API regression, browser E2E, and short load gate against an isolated test stack.
- [x] Build and smoke the production frontend artifact; Lighthouse thresholds pass on Vite's production preview. No production image/Compose definition exists, so container/deployment smoke and a release tag are not applicable yet.
- [ ] Human: run demo offline twice and ask a first-time user to complete kiosk flow without assistance.

## Exit notes

Automated implementation items may pass independently from manual clinical, language, and usability review. Never report the manual items complete without direct observation.

API: 761 passed; frontend: 37 unit tests and 116 E2E tests passed. Lighthouse, synthetic evaluation, and the health-only k6 smoke passed. See [phase-14.md](../phase-reports/phase-14.md) for measured results and remaining gates.
