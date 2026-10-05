# Phase 5 manual plan — queue, token, and live position

Use synthetic demo data only. Phase 4 consent and interview content is still awaiting clinician and native-speaker review; do not use this kiosk path with real patients.

## Start the stack

1. Set private development credentials in `.env`, including `OSA_WORKER_PASSWORD`. Existing Postgres volumes get the worker role from the Compose bootstrap service; do not delete the volume to apply this change.
2. Run `make up` and `make migrate`, then run `make api`, `make worker`, and `make web` in separate terminals.
3. Open `http://localhost:5173/kiosk`, activate a synthetic kiosk, and complete one English, Hindi, or Marathi intake using invented answers.

## Follow a patient through the queue

1. Confirm the case-sheet summary. The kiosk displays a department token and QR receipt; use the print button to check the paper layout.
2. Open the QR link on a second browser or phone. Confirm that it shows the token, people ahead, and a wait range, without exposing urgency or clinical details.
3. Sign in at `/staff` with a seeded synthetic nurse account. Call the waiting token and confirm the patient status changes. A doctor account can start and complete the visit.
4. Check the browser's reconnect path by briefly stopping Redis. The API writes remain in PostgreSQL and the ARQ worker relays pending queue events after Redis returns.
5. Run `python -m app.cli queue-rebuild` and confirm the waiting order matches the staff board.
6. For a larger synthetic board, create a dedicated demo kiosk and provide its one-time code as `SIMULATE_KIOSK_CODE`, then run `make simulate-queue N=50 URGENT=10`. Repeated runs can use the activated kiosk token as `SIMULATE_KIOSK_TOKEN`.

The queue implementation uses a temporary age-based department route for this phase: younger than 14 goes to `PAED`; everyone else goes to `GENMED`, when those active departments exist. Danger tiles set the temporary urgent flag. Phase 6 replaces these defaults with reviewed routing and red-flag rules.
