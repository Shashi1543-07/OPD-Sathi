# Phase 04 — Deterministic interview and case sheet (2026-10-06, local worktree)

**Status:** Local implementation and gates pass. This worktree has uncommitted Phase 4 changes; remote CI and explicit human approval remain pending. Base commit at handoff: `3bca3c3`.

## 1. In plain words

The kiosk now guides a patient from consent through a short interview and a reviewable summary in English, Hindi, or Marathi. The questions follow fixed rules and do not use AI or voice input. Patients can skip optional questions and correct earlier answers; danger-tile selections are recorded for staff. The case sheet is a draft of the patient's own words, and the next phase will replace the temporary confirmation message with a real queue token.

```text
Activate → Consent → Details → Danger tiles → Interview → Review / change → Confirm
```

## 2. What was built

- **4.1–4.2:** Versioned multilingual consent, consent gate, validated identity fields, encrypted optional phone storage, and danger-tile event recording.
- **4.3:** English, Hindi, and Marathi question bank, 13 modules, trigger map, review-status/content validation, and broken-bank fixtures.
- **4.4–4.6:** Pure deterministic interview engine with a 12-question cap, bounded input cleaning, corrections, case-sheet validation, and language-specific read-back.
- **4.5, 4.7:** Redis session state mirrored by interview turns, idempotent answer submission, audit events for consent and confirmation, confirmation state, and a Phase 5 placeholder response.
- **4.8:** Kiosk path for consent, identity, danger tiles, touch/text interview, optional skip, answer correction, summary, confirmation, and safe cancellation. Voice control remains disabled.
- **4.9:** 30 multilingual scripted golden interviews and a reproducible evaluator.
- **4.10:** Three locale-specific browser flows covering a danger tile, skip, correction, summary accessibility, and confirmation.
- Created [the Phase 4 manual plan](../phase-plans/phase-04.md) and [clinical review queue](../CLINICIAN_REVIEW.md); updated README and project checklist.

## 3. Results against the gate

| Gate item | Threshold | Measured | Pass |
|---|---:|---:|---|
| Golden interviews | 30/30 | 30/30 | Yes |
| Interview domain coverage | ≥95% | 99% (bank 100%, consent 100%, engine 99%) | Yes |
| Property tests | 500 generated valid sequences | 500 Hypothesis examples; backend suite passed | Yes |
| Browser flow | English, Hindi, Marathi | 3/3 Playwright tests passed | Yes |
| Tap count | ≤25 typical path | 14 button/scale clicks in the scripted full flow, including a danger tile and correction | Yes |
| Scripted answer time | ≤180 seconds at 4 sec/answer | 4 answer/edit submissions × 4 sec = 16 sec in the browser scenario; 12-question hard-cap bound = 48 sec | Yes |
| Malicious/oversized input | 400 or safe cleaning | 10,000-character payload returns generic 400; 500-character max enforced; control characters normalized; HTML/SQL-like text and emoji treated as text | Yes |
| Consent required | All protected intake actions blocked without consent | Unit/API authorization test checks 409 `CONSENT_REQUIRED` | Yes |
| Clinical content review | Draft until clinician sign-off | Review queue created; all listed content remains pending | Pending human review |

Local output:

```text
Golden interview evaluation: 30/30 passed
Validated 16 questions across 13 modules.
128 backend tests passed; 99% interview-domain coverage.
16 frontend unit tests passed.
3 Phase 4 Playwright tests passed (English, Hindi, Marathi).
Ruff: All checks passed.
mypy: Success, no issues found in 40 source files.
```

The 16-second figure is the four-seconds-per-answer script applied to the four answer/edit submissions in the browser scenario; it is not a measurement of network or human response latency. The 48-second figure is the same script applied to the 12-question engine cap. Clinical review is a required human gate before real use, not an automated content-quality result.

## 4. Commands run

- `.venv\Scripts\python.exe -m pytest services/api/tests -q --cov=services/api/app/domain/interview --cov-report=term-missing` — **128 passed**, 0 failed, 0 skipped; interview-domain coverage **99%**; one Starlette/httpx deprecation warning.
- `$env:PYTHONPATH='services/api'; .venv\Scripts\python.exe eval/interview/evaluate.py` — **30/30** golden interviews.
- `$env:PYTHONPATH='services/api'; .venv\Scripts\python.exe services/api/scripts/validate_question_bank.py` — **16 questions / 13 modules validated**.
- `.venv\Scripts\ruff.exe check services/api/app services/api/tests services/api/scripts` — passed.
- `.venv\Scripts\mypy.exe --strict services/api/app` — passed; 40 source files checked.
- `npm run lint --prefix apps/web` — passed.
- `npm run typecheck --prefix apps/web` — passed.
- `npm run test --prefix apps/web -- --reporter=dot` — **16 passed**.
- `npm run e2e --prefix apps/web -- phase-04.spec.ts` — **3 passed** across English, Hindi, and Marathi; no serious or critical axe violations in the summary screen.
- `npm run build --prefix apps/web` — passed; production bundle **126.30 KB gzip**, PWA service worker generated.
- `npm run check-locales --prefix apps/web` — passed; **206 keys per language** across four namespaces.
- `npm run check-contrast --prefix apps/web` — passed; **12 documented text pairs** meet the contrast thresholds.

## 5. Screenshots and artifacts

- Browser scenarios: [`apps/web/e2e/phase-04.spec.ts`](../../apps/web/e2e/phase-04.spec.ts)
- Golden interviews: [`eval/interview/golden/interviews.jsonl`](../../eval/interview/golden/interviews.jsonl)
- Evaluation command: [`eval/interview/evaluate.py`](../../eval/interview/evaluate.py)
- No Phase 4 screenshot artifact was captured in this handoff; the browser run used mocked API responses.

## 6. How the human tests this phase

Follow the [Phase 4 manual demo](../phase-plans/phase-04.md). Use only synthetic answers. Verify the consent gate, try a danger tile, complete the interview, change an earlier answer, review the case sheet, and confirm. Oversized text should be rejected without exposing raw input in an error. Do not use live patients until the items in [the clinical review queue](../CLINICIAN_REVIEW.md) have been signed off.

## 7. Known issues and decisions

- The confirmation endpoint deliberately returns a placeholder; token allocation and queue registration belong to Phase 5.
- Consent, questions, triggers, and danger tiles are drafts pending clinician and native-speaker review. No clinical sign-off is implied by software tests.
- The local suite emits one existing Starlette/httpx deprecation warning. GitHub CI was not run from this local worktree.
- The worktree contains in-progress changes from the prior agent, including refreshed Phase 1 screenshots; those files were preserved. Nothing was committed to avoid bundling unrelated pre-existing changes.

## 8. Next phase preview

Phase 5 implements daily token allocation, queue ordering, live position, and recovery when Redis is unavailable. It remains gated on explicit Phase 4 approval.

Reply `approve phase 4` to continue.
