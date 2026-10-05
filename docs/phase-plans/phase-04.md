# Phase 4 plan: deterministic interview and case sheet

**Status:** Local implementation and automated gates are complete. Human phase approval and remote CI remain pending. This phase follows [the build playbook](../../AGENT_PLAYBOOK.md#phase-4--deterministic-interview-and-case-sheet-f2-consent-f4-core-f5).

## What the phase delivers

The kiosk supports language selection, activation, consent, identity, danger tiles, a deterministic touch-and-text interview, a patient-reviewed case-sheet summary, and confirmation. English, Hindi, and Marathi copy is versioned and validated. The interview has a 12-question hard limit, stores turns, and uses no AI or voice input. Confirmation returns a temporary token message until Phase 5 implements queue registration.

## Manual demo

1. Start PostgreSQL and Redis, apply migrations, and provision a demo hospital and kiosk using the [Phase 3 setup](phase-03.md#manual-demo-steps). Start the API and web app.
2. Open `/kiosk`, select Marathi, enter the kiosk's one-time activation code, and start check-in.
3. Review and accept the consent. Enter a synthetic age and sex; leave phone blank for this demo.
4. Enter no danger tile for a routine path. Describe a synthetic concern such as “fever for three days”; answer or skip optional prompts.
5. Review the structured summary, use Change on an earlier answer, and confirm. The page should show the temporary queue-registration message.
6. Repeat in Hindi and select a danger tile. A neutral instruction to tell staff should appear, and the event should be attached to the visit.
7. Try an answer longer than 500 characters, HTML-like text, SQL-like text, control characters, and emoji. Oversized input should be rejected safely; accepted text should remain patient-provided data and render as text.

All data must be synthetic. Question-bank, danger-tile, routing, and consent copy remain drafts pending clinician and native-speaker review; see [the review queue](../CLINICIAN_REVIEW.md).
