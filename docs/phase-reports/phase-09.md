# Phase 09 — AI layer: smart questions, extraction, second opinion (6 October 2026, `phase-09-ai-layer`)

## 1. In plain words

The AI can help choose from approved questions, organize patient statements into history fields, and raise (never lower) a red-flag level. Every extracted fact must be supported by a quote from the transcript, and unsafe output is filtered. The doctor view shows which fields were AI-assisted, their source, and confidence. Mock results validate that the pipeline behaves safely; they do not measure Gemini's medical-language quality.

## 2. What was built

- **9.1–9.2:** Recorded the Gemini provider decision and added JSON-schema provider interfaces, Mock and Gemini implementations, timeouts, retries, circuit breaking, per-session call limits, and safe metrics.
- **9.3–9.4:** Added PII redaction and versioned, delimited prompts with safety instructions and hashes.
- **9.5–9.8:** Added evidence-quoted extraction, constrained approved-question selection, monotonic red-flag merge, and fixed-label routing with nurse confirmation on disagreement.
- **9.9–9.10:** Added multilingual unsafe-output guards, generic-text caching, tenant enablement, and cost controls.
- **9.11:** Added 100 extraction dialogues (40 English, 30 Hindi, 30 Marathi), 100 adversarial prompts, Mock-based red-flag pipeline evaluation, and Make/CI targets.
- **9.12:** Added case-sheet provenance icons, confidence chips, and an AI-assisted marker with English, Hindi, and Marathi translations.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---:|---:|---|
| Extraction F1 (synthetic Mock) | ≥0.90 | 1.00 (170 TP, 0 FP, 0 FN; 100 fixtures) | Pass, pipeline only |
| Unsupported extraction fields | ≤2% | 0/170 (0%) | Pass, pipeline only |
| Diagnosis/advice leaks in adversarial set | 0/100 | 0/100; 100/100 blocked | Pass, guard fixtures |
| Red-flag Mock fallback recall | ≥99% overall | 289/289 expected flags (1.00) | Pass, fallback only |
| Red-flag must-catch | 60/60 | 60/60 | Pass, fallback only |
| Urgency monotonicity | Never decreases | 500/500 cases | Pass |
| Routing top-1 (rules-only, dev/test) | ≥85% | 224/224 dev (1.00), 96/96 test (1.00) | Pass, rules-only |
| Combined rules + Gemini routing | ≥92% | Not measured; no live provider run was authorized in this handoff | Not measured |
| Real Gemini extraction/adversarial/red-flag quality | Gate results from live provider | Not measured; no live provider run was authorized in this handoff | Not measured |
| PII redaction tests | 100% | Covered by existing unit tests; count not separately rerun for this report | Not measured here |
| Frontend unit tests | 0 failed | 35 passed, 11 files | Pass |
| Frontend typecheck and production build | Clean/succeeds | Both passed; 1,699 modules, JS 495.30 kB (153.90 kB gzip) | Pass |
| Browser provenance/accessibility check | 0 failed; no serious/critical axe violations | 1 passed; 0 serious/critical violations | Pass |
| Targeted AI backend tests | 0 failed | 281 passed | Pass |
| Ruff on changed AI/API/evaluation files | Clean | All checks passed | Pass |

The required live-provider gates remain outstanding. All synthetic fixtures contain scripted data, and the evaluation scripts explicitly label Mock results as pipeline/fallback measurements rather than model-quality results.

## 4. Commands run

- `PYTHONPATH=services/api python eval/extraction/evaluate.py` — 100 fixtures; F1 1.00; unsupported fields 0.
- `PYTHONPATH=services/api python eval/adversarial/evaluate.py` — 100 prompts; 100 blocked; 0 leaks.
- `PYTHONPATH=services/api python eval/redflags_llm/evaluate.py` — 500 cases; recall 1.00; 60/60 must-catch; 500/500 monotonic.
- `PYTHONPATH=services/api python eval/routing/evaluate.py` — 224 dev and 96 test cases; top-1 1.00 each (rules-only).
- `PYTHONPATH=services/api python -m pytest services/api/tests/unit/test_ai_extraction.py services/api/tests/unit/test_ai_output_guard.py services/api/tests/unit/test_ai_redflag_opinion.py services/api/tests/unit/test_ai_department_classifier.py services/api/tests/unit/test_ai_question_choice.py services/api/tests/unit/test_llm_redaction.py -q` — 281 passed.
- `python -m ruff check services/api/app/ai services/api/app/api/v1/intake.py services/api/app/domain/case_sheet.py services/api/tests/unit/test_ai_extraction.py eval/extraction eval/adversarial eval/redflags_llm` — passed.
- `npm --prefix apps/web test -- --run` — 35 passed (11 files).
- `npm --prefix apps/web run typecheck` — passed.
- `npm --prefix apps/web run build` — passed.
- `npm exec -- playwright test e2e/phase-09.spec.ts` (from `apps/web`) — 1 passed; no serious or critical axe violations.

On Windows the native Vitest command initially failed with sandbox `spawn EPERM`; rerunning the frontend checks with the required command approval succeeded. WSL was unavailable to the sandboxed run; the project virtual environment (`.venv`, Python 3.12.15) ran the Python evaluations directly.

## 5. Screenshots and artifacts

- `eval/extraction/dialogues.jsonl` — 100 synthetic dialogues.
- `eval/adversarial/prompts.jsonl` — 100 synthetic adversarial prompts.
- `eval/redflags_llm/` — Mock fallback evaluation.
- `docs/screenshots/phase-09/staff-case-sheet.png` — staff provenance UI.
- `eval/live/evaluate.py` — opt-in live-provider evaluation harness; not run in this handoff.

## 6. How the human tests this phase

1. Complete a voice intake with a detailed synthetic complaint; the staff case sheet should display organized history fields and source/confidence metadata.
2. Ask for a disease or medicine in the kiosk interview; the flow should continue approved history questions without returning diagnosis or advice.
3. Disable `LLM_ENABLED` or make the provider unavailable; deterministic questions and red-flag rules should continue without a patient-facing error.

## 7. Known issues and decisions

- Live Gemini routing accuracy, extraction accuracy, and red-flag recall have not been measured. These require provider credentials and a live-provider run using synthetic prompts.
- The report's Mock extraction and guard figures demonstrate fixture/pipeline behavior, not live model performance.
- Clinical review of red-flag and routing content and native-speaker review remain pending as tracked elsewhere in the project.

## 8. Next phase preview

Phase 10 adds multilingual read-back, fixed prompt audio, patient correction and confirmation, and staff visibility of confirmation status. Dynamic audio will not be cached because it contains patient data.
