# Phase 12 implementation plan

## Scope

Keep kiosk registration usable during a network outage with a limited short form, durable local submissions, and idempotent server replay. Prescription upload and extraction are documented stretch work and are excluded from the required offline-intake gate.

## Build sequence

1. **12.1 PWA shell** — precache the kiosk shell, styles, fonts, locale resources, prompt audio, and manifest; verify an installed kiosk opens without network. Add an update notice for staff and defer kiosk activation until idle.
2. **12.2 Connectivity state** — combine browser online/offline events with a `/healthz` heartbeat every ten seconds; show the localized offline banner. Do not imply that a submission has been saved until it is in the local outbox.
3. **12.3 Offline short form** — provide danger tiles, typed/common-category complaint, duration, severity, conditions, medicines, and allergies. Do not duplicate the adaptive interview engine. A danger selection immediately directs the patient to the nurse desk.
4. **12.4 Local outbox** — store validated submissions in IndexedDB with cryptographic idempotency keys, retry with backoff, show pending-sync state, and issue a provisional local token that cannot be confused with a server queue token.
5. **12.5 Server replay** — add a kiosk-authenticated endpoint that replays a submission through the existing clinical engine and queue path. Persist idempotency state so retries after a disconnect or server restart return the same real token and never create duplicate visits.
6. **12.6–12.7 Stretch** — only after the offline gate passes, add bounded prescription upload and reviewed extraction.
7. **12.8 Verification** — test offline completion, danger-message timing, duplicate and interrupted replay, accessibility, and PWA installability; document manual steps and measured limits in the phase report.

## Security and privacy constraints

- Keep the offline form limited to information necessary for the intake; never put data in service-worker caches or logs.
- Protect local records, delete them after confirmed sync, and never treat provisional token numbers as server-authoritative.
- Replay all answers through the existing engine, including red-flag evaluation; do not trust client-computed urgency or department routing.
- Do not mark prescription extraction as confirmed clinical fact.

## Current progress

- **12.1 complete:** production service worker precaches the kiosk app shell, styles, fonts, locale bundles, prompt audio, and manifest; offline playback is verified.
- **12.2 complete:** kiosk shell combines browser online/offline events with a ten-second `/healthz` heartbeat and localized offline banner.
- **12.3 complete:** offline short form captures consent, identity basics, complaint, duration, severity, medical history, medicines, allergies, and danger signs. Danger signs immediately direct the patient to the nurse desk.
- **12.4 complete:** submissions are encrypted in IndexedDB with a non-extractable AES-GCM key, provisional local tokens, retry backoff, pending status, and removal after server confirmation.
- **12.5 complete:** kiosk-authenticated replay validates current consent, runs answers through the existing interview, red-flag, routing, case-sheet, and queue logic, and uses shared Redis idempotency state.
- **12.8 complete for the required offline gate:** browser offline/save/reconnect flow, danger alert, accessibility scan, real PostgreSQL/Redis replay, duplicate-after-restart replay, PWA cached asset, and Lighthouse gates are recorded in `docs/phase-reports/phase-12.md`.
- **12.6–12.7 excluded:** prescription upload and extraction remain stretch work and are not part of the required offline-intake scope.
