# Clinical and language review queue

All patient-facing consent, question-bank, danger-tile, and red-flag content below is a draft. A clinician and native speakers for Hindi and Marathi must review it before any live-patient use. The deterministic engine collects patient statements; it does not diagnose or recommend treatment.

| Content | Review needed | Status |
|---|---|---|
| `services/api/app/domain/interview/consent/` | Clinical/legal consent wording and English, Hindi, and Marathi translations | Pending |
| `services/api/app/domain/interview/bank/core.json` | Clinical relevance, neutral wording, translation parity | Pending |
| `services/api/app/domain/interview/bank/modules/` | Clinical relevance, completeness, neutral wording, translation parity | Pending |
| `services/api/app/domain/interview/bank/triggers.json` | Clinical appropriateness and whether complaint terms select suitable questions | Pending |
| `services/api/app/domain/interview/bank/danger_tiles.json` | Emergency wording, escalation expectations, translation parity | Pending |
| `services/api/app/domain/interview/engine.py` | Confirm deterministic field mapping and default routing are safe for history collection | Pending |

No clinical sign-off is implied by passing automated checks. Track reviewer names, dates, and approved content versions here after review.
