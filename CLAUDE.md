# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

_Sections 1–4 are from [andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) (MIT)._

---

# OPD Saathi

A voice-first patient history-taking and queue kiosk for government hospital OPDs. Read
`README.md` for the product, `CONTRIBUTING.md` for the rules every change follows, and
`docs/DECISIONS.md` for why things are the way they are.

## Never

- **Diagnose.** No product text or AI prompt may name a disease, suggest a medicine or dose,
  give advice ("you should…") or reassure. The AI may raise urgency, never lower it.
- **Use real patient data.** Synthetic data only. Never commit `.env`, keys, dumps or logs, and
  never print secret values.
- **Log personal data.** No answers, names, phone numbers, tokens or codes in logs
  (`services/api/tests/security/test_no_pii_in_logs.py` enforces it).
- **Weaken a test to get green.** No lowered thresholds, skips or deleted tests. If a gate
  cannot be met, stop and report.
- **Change clinical content without saying so.** The files listed under "Clinical change
  control" in `CONTRIBUTING.md` need a clinician's review; flag any edit to them.
- **Add a framework, database or major library** without an ADR in `docs/DECISIONS.md` and the
  owner's approval.

## Layout

- `services/api`: FastAPI + SQLAlchemy + Alembic (Python 3.12), plus the background worker.
- `apps/web`: React + Vite PWA: `/kiosk`, `/staff`, and the patient's phone page `/s/<token>`.
- `eval/`: golden sets for the interview, red flags, routing and extraction.
- `infra/`, `docker-compose*.yml`: Postgres, Redis, nginx, monitoring.

## Working here

- Work on a branch and open a PR into `main`, which is protected: CI must pass first.
- Before calling a change done, run the checks CI runs for the part you touched
  (`.github/workflows/ci.yml`): `ruff`, `mypy --strict services/api/app` and `pytest` for the
  API; `npm run lint`, `typecheck`, `test` and `e2e` in `apps/web`. Run `make eval` after any
  change to the interview, red flags, routing or prompts.
- Regenerate checked-in artifacts after changing what they come from: the OpenAPI types
  (`python services/api/scripts/write_openapi.py` then `npm run types`), the case-sheet schema,
  and the prompt audio manifest (`make prompts-audio-check`).
- Migrations follow expand → migrate → contract and need a working `downgrade()`.
- Queue and admin POSTs need an `Idempotency-Key` header.
- Every patient-facing string exists in English, Hindi and Marathi (`npm run check-locales`).
- On Windows, use `127.0.0.1` rather than `localhost`, and run the full demo with
  `.\start-demo.ps1` (Docker). PDF generation (WeasyPrint) only works inside Docker.
