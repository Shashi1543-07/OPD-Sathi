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
| `services/api/app/domain/redflags/rules.yaml` | Clinical appropriateness, false-negative/false-positive risks, urgency, Hindi/Marathi/Hinglish wording | Pending |
| `services/api/app/domain/routing/engine.py` | Department mapping, age/pregnancy modifiers, ambiguity and nurse-triage behavior | Pending |
| Red-flag patient messages in `services/api/app/api/v1/intake.py` | Confirm neutral wording and Hindi/Marathi translations | Pending |
| Phase 14 judge-mode disclaimer and staff “How it works” panel | Clinician safety wording plus Hindi/Marathi translation review before localized use | Pending |

| `services/api/app/domain/triage/weights.yaml` | Every weight and threshold; that a patient's own rating cannot move them far | Pending |

No clinical sign-off is implied by passing automated checks. Track reviewer names, dates, and approved content versions here after review.

## Marking content approved (task 7.6.1)

After the review, change the file's status from `draft_needs_clinician_review` to `approved`
and add the review record, in the same pull request as the updated row above
(`CONTRIBUTING.md`, "Clinical changes"). The loaders refuse `approved` without a complete record
(`services/api/app/domain/review.py`): at least one named reviewer with their qualification, a
date not in the future, and the content version reviewed.

Question bank, triggers and danger tiles (JSON), and the triage weights (YAML), at the top level:

```json
"review_status": "approved",
"review": {
  "reviewed_by": ["Dr A. Kulkarni, MD (General Medicine)", "S. Patil, native Marathi reviewer"],
  "reviewed_on": "2026-11-20",
  "content_version": "core-1.1"
}
```

Red-flag rules (`rules.yaml`), per rule, so that each rule is signed off on its own:

```yaml
- id: RF_CHEST_PAIN_ASSOC
  level: urgent
  status: approved
  review: {reviewed_by: ["Dr A. Kulkarni, MD"], reviewed_on: 2026-11-20, content_version: rf-1.0}
```

Consent notices (front matter; the notice's own `version` is the content version):

```
---
review_status: approved
version: consent-1.1
reviewed_by: Dr A. Kulkarni, MD; Adv. R. Shah (counsel); S. Patil (Marathi)
reviewed_on: 2026-11-20
---
```

**Before live patients**, set `CLINICAL_CONTENT_APPROVAL_REQUIRED=true` in the installation's
`.env` (`docs/GO_LIVE_CHECKLIST.md`). The API then refuses to start while any content is still a
draft, and names the files. `python -m app.cli content-status` lists each file's status.
