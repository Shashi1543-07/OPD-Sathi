# Phase 14 — Demo polish and acceptance (7 October 2026)

## Delivered

- `make demo-seed` provisions the fictional Nagpur hospital and synthetic demo accounts using the repeatable seed command.
- `PROVIDER_MODE=demo-safe` selects local STT/TTS mocks and schema-bounded saved LLM fixtures. `DEMO_STORY` selects Hindi fever, Marathi chest pain with sweating, or English child cough scenarios.
- Judge Mode (`?demo=1`) adds a localized history-taking disclaimer, a measured microphone-start-to-final-transcript latency badge, and an optional localized staff “How it works” panel. Development-only routes remain guarded by Vite's `DEV` flag.
- Added a configurable hospital logo slot, first-paint splash, PWA icon metadata, screenshot generator, Mermaid architecture, updated quickstart/demo/evaluation documents, clinician review queue, and a backend-to-UI audit.
- Removed the unfinished admin landing placeholder by routing staff landing to the operational queue and admin landing to Reports. Role boundaries and `?demo=1` navigation are covered in the browser suite.
- Split intake, staff queue/consultation/reports/settings, patient status, offline intake, and development-only routes into independently loaded chunks. The production entry chunk is now under Vite's 500 kB advisory.
- Screenshot artifacts for patient, nurse/reception, doctor, and admin are saved under `docs/screenshots/phase-14/`. E2E-generated changes to pre-existing screenshot paths were restored from a pre-run copy.

## Automated evidence

| Gate | Result |
|---|---|
| API regression | 761 passed; 82% aggregate coverage; one Starlette/httpx deprecation warning |
| Frontend unit suite | 37 passed across 12 files |
| Browser E2E | 116 passed, sequential, against the isolated synthetic stack |
| Production web build | Passed; entry JavaScript is 427.86 kB minified (135.36 kB gzip); operational screens are separate chunks |
| Frontend lint and TypeScript | Passed |
| Locale key parity | Passed: English, Hindi, Marathi; 407 keys per language across four namespaces |
| Contrast | Passed: all 12 documented text pairs meet WCAG AA 4.5:1 |
| Lighthouse | Passed all thresholds across six post-split production-preview runs for `/kiosk` and `/s/demo`; accessibility 100 throughout, performance 88–89 and 96 respectively, CLS 0.000–0.067 |
| Axe in role flows | Zero serious/critical findings in audited Phase 7 and Phase 11 screens |
| Large queue interaction | 5,000 rows; urgent update in 71.90 ms against a <100 ms threshold |
| Synthetic evaluation | Interview 30/30; routing test 96/96; red-flag must-catch 60/60 and negation 50/50; extraction 100 fixtures, 170 fields, 0 unsupported; adversarial guard 100/100 blocked; monotonic urgency 500/500 |
| Isolated API smoke | `/healthz` 200 and `/readyz` 200 |
| k6 health smoke | 1,387 checks, 0 failures; max 25 VUs; 19.74 requests/s; p95 5.12 ms |
| Dependency/source security | npm audit: 0 vulnerabilities; pip-audit: no known vulnerabilities in both Python lockfiles; Bandit passed; Gitleaks found no leaks across 82 commits after four exact synthetic test-fixture fingerprints were added to `.gitleaksignore` |

The API and browser runs used the separate `opd-saathi-phase14` Compose project, with synthetic seed data and separate ports/volumes. Existing Phase 13 volumes were left untouched. The k6 test exercises only `/healthz`; it is not representative intake, queue, WebSocket, or sustained-load evidence.

Synthetic evaluation fixtures validate deterministic code paths, not clinical accuracy or live-model quality. Lighthouse includes a single transient kiosk performance score of 84 on its first six-run attempt; the repeat passed all configured thresholds. The final six-run set passed.

## Manual and release gates still open

- Human acceptance remains: run the three-minute demo twice with internet disabled, then have a first-time user complete kiosk intake without coaching.
- Clinician and native-speaker review remains open for clinical rules, patient questions, consent, and Hindi/Marathi copy. See [CLINICIAN_REVIEW.md](../CLINICIAN_REVIEW.md).
- The in-app browser bridge failed with `missing field sandboxPolicy` during this handoff. Automated browser runs and saved screenshots were used; interactive in-app inspection is not verified.
- No production Compose definition exists, so production-container smoke and deployment validation were not run. No release tag was created.
- Broader Phase 13 production gates remain open as listed in [phase-13.md](phase-13.md), including full security scans, representative workload/soak tests, and operational policy review.
- Semgrep auto-rule execution did not produce results before it stalled and was stopped; Trivy image scanning, ZAP, Schemathesis, representative API/queue/status/WebSocket load profiles, query-count/scale checks, expanded telemetry, and the 30-minute soak remain open.

Phase 14 implementation and automated acceptance checks are complete. Human usability, clinical/language review, and production release gates remain outstanding; this report does not claim v1.0 production readiness.
