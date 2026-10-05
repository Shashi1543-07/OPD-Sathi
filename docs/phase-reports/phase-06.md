# Phase 06 — Red flags and department routing (6 October 2026, branch `phase-06-redflags-routing`)

**Status:** Implementation and local automated gates pass on `phase-06-redflags-routing`. Clinical and Hindi/Marathi native-speaker review remains pending before real-patient use. The implementation was started from the merged Phase 5 base; this branch has not been pushed or merged.

## 1. In plain words

The intake now checks patient statements and danger-sign selections against a deterministic set of draft rules. Staff alerts are sent over the hospital and department-scoped WebSocket path, while patients receive only a neutral message. A separate rules-based router selects a department or sends uncertain cases to nurse triage. The English, Hindi, Marathi, and Hinglish rule text remains draft content and the synthetic scores below are software checks, not clinical accuracy claims.

```text
Patient answer → red-flag rules → durable event → nurse alert
               → routing rules → department or nurse triage
```

## 2. What was built

- **6.1–6.4:** Unicode-aware normalization; validated draft rules with required positive and negative examples; deterministic matching with structured conditions, negation scope, deduplication, and monotonic urgency.
- **6.5:** Explainable department routing with age and pregnancy context, confidence, and a nurse-triage fallback.
- **6.6:** Intake and summary integration, durable deduplicated events, queue reprioritization, and neutral patient-facing responses. Self-harm details stay staff-only.
- **6.7:** Hospital/department-scoped alert listing, acknowledgement audit, staff WebSocket notifications, 60-second escalation, alert banner, and opt-in chime.
- **6.8–6.9:** Reproducible synthetic datasets, rule-example validation, evaluation commands, and recorded split results.
- **6.10:** Confirmation uses the real routing engine and the staff queue board can override the route with a reason.
- Added tests for previously uncovered rule-validation and structured-condition branches, plus a Redis-to-staff-WebSocket alert timing check.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---:|---:|---|
| Draft rules and unit examples | Every rule has at least 3 positive and 2 negative examples | 16 rules; 48 positive and 32 negative examples | Pass |
| Red-flag corpus | At least 200 scripts, including 60 must-catch, 50 negation, 30 benign | 500 scripts; test split 150, including 60 must-catch, 50 negation, 70 benign | Pass |
| Red-flag recall, test split | ≥98% overall; 100% must-catch | 100% (84/84 expected flags); 60/60 must-catch | Pass |
| Red-flag precision, test split | ≥70% | 100% (84/84 predicted flags) | Pass |
| Negation cases, test split | ≥90% correct | 100% (50/50) | Pass |
| Routing top-1, test split | ≥85% | 100% (96/96) | Pass |
| Staff alert delivery | <1 second p95 | 12.56 ms p95 over 100 real Redis pub/sub to staff-WebSocket messages | Pass |
| Patient self-harm privacy | No self-harm details in patient response | Patient case-sheet redaction test passes; full backend suite passes | Pass |
| Red-flag and routing domain coverage | ≥95% | 100% (165/165 statements) | Pass |

The test corpus is generated from the same phrase vocabulary as the rules. Its perfect scores only show that the implementation matches authored examples; they do not estimate clinical sensitivity, translation quality, or real-world false-positive rates. The alert timing test uses real local Redis and the WebSocket route with the database authorization lookup stubbed; it is a local development measurement, not a production SLO claim.

## 4. Commands and checks

- `python -m pytest services/api/tests -q` (Python 3.12 container, CI-equivalent environment): **319 passed**, 0 failed, 0 skipped; one Starlette/httpx deprecation warning.
- Full-suite coverage report: **99% (930/939 statements)** for `app/core` and `app/domain`, above the CI threshold of 95%.
- Phase 6 focused tests with coverage: **117 passed**; red-flag and routing domains **100% (165/165 statements)**.
- Alert timing integration test: **1 passed**, 100 messages, **12.56 ms p95**.
- Red-flag rule validator: **16 rules, 48 positive, 32 negative examples**.
- Red-flag evaluation: **500 scripts** (350 dev / 150 test); test results **recall 100%, precision 100%, must-catch 60/60, negation 50/50**.
- Routing evaluation: **320 cases** (224 dev / 96 test); test top-1 **96/96 (100%)**.
- Ruff: passed. Strict mypy: passed for **48 application files**.
- Frontend: **16 Vitest tests passed**; TypeScript typecheck and ESLint passed; production build passed (**408.27 kB JS, 129.59 kB gzip**).

## 5. Screenshots and artifacts

- [Synthetic evaluation results](../EVALUATION.md)
- [Clinician and native-speaker review queue](../CLINICIAN_REVIEW.md)
- [Manual verification plan](../phase-plans/phase-06.md)
- [Red-flag unit tests](../../services/api/tests/unit/test_redflags.py)
- [Alert delivery measurement](../../services/api/tests/integration/test_redflag_alert_delivery.py)
- [Red-flag evaluation data](../../eval/redflags/scripts.jsonl)
- [Routing evaluation data](../../eval/routing/cases.jsonl)

## 6. How the human tests this phase

Use only fictional visits and staff accounts. Follow the [Phase 6 manual plan](../phase-plans/phase-06.md): submit “chest pain and sweating,” confirm an alert and queue urgency change, acknowledge it, then submit “no chest pain” and verify no chest-pain alert. Check that an unclear concern goes to `NURSE_TRIAGE`, and that a staff member assigned to another department cannot list or acknowledge the first department’s alert. Do not use the self-harm test wording unless a clinician authorizes that synthetic test. Patients must see only the neutral staff-help message.

## 7. Known issues and decisions

- Every rule, routing destination, and translated phrase is still marked draft and awaits clinician and native-speaker review. Do not use the rules for unsupervised triage or with real patients.
- The synthetic corpus shares wording with the rule bank by design; independent, clinician-labelled evaluation is still needed.
- GitHub CI has not run on this unpushed branch. Local automated gates listed above pass.
- No screenshots were captured during this handoff.

## 8. Next phase preview

Phase 7 builds the doctor and nurse consoles, audited case-sheet reads and edits with version conflicts, and a multilingual PDF export. The existing staff shell and queue board provide the starting point; the next workstream is `phase-07-consoles`.
