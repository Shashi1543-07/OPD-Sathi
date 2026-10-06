# Phase 6 manual verification plan

Use synthetic visits and staff accounts only. The rules and translations are drafts; do not test with real patient information.

## Red-flag flow

1. Start the local Compose stack, migrate the database, and sign in to the kiosk and staff board.
2. Complete a synthetic intake with “chest pain and sweating”. Confirm the visit and verify that its queue urgency rises and the staff board displays one alert.
3. Acknowledge the alert. Verify it disappears from the unacknowledged list and appears in the acknowledged list.
4. Complete another intake with “no chest pain”. Verify no chest-pain alert is created.
5. Select a danger-sign help tile. Verify the patient sees the neutral staff-help prompt and the board receives an alert.
6. Use a synthetic self-harm statement only if the clinician has authorized this test wording. Confirm the staff alert is visible and no self-harm flag, reason, or evidence is returned by any patient-facing case-sheet or status response.
7. Sign in as staff assigned to a different department and verify the first department's alert is not visible or acknowledgeable.

## Department routing

1. Try one clear concern for each department represented in the routing cases. Verify the selected queue matches the synthetic case expectation.
2. Try an unclear concern. Verify the case is sent to `NURSE_TRIAGE` and marked for staff confirmation.
3. Confirm a route as a nurse and verify the decision reason is recorded in the audit log.

## Automated commands

```sh
make eval-redflags
make eval-routing
```

These corpora are synthetic and derived from the rule vocabulary. Passing them verifies implementation behavior against authored examples, not clinical performance. See [evaluation results](../EVALUATION.md) and the [clinician review queue](../CLINICIAN_REVIEW.md).
