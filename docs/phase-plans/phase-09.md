# Phase 09 plan — AI layer

**Branch:** `phase-09-ai-layer` (stacked on approved `phase-08-voice`, commit `f25f576`)
**Scope:** Playbook steps 9.1–9.12. Mock remains the default; no patient text is sent to Gemini unless an operator explicitly configures the provider and API key.

## Build sequence

| Step | Work | Evidence before the next step |
|---|---|---|
| 9.1 | Re-check official Gemini model and structured-output docs. Record model IDs, SDK, schema behavior, timeout/retry constraints and data handling in ADR 004. | ADR reviewed against official docs; schema constraints documented. |
| 9.2 | Add the `LLMProvider` protocol, strict JSON boundary, scripted/failing `MockLLM`, async Gemini adapter, timeout, one retry, circuit breaker, token metrics and 30-call session cap. Pin the SDK. | Provider contract, schema, retry, timeout, circuit and call-cap tests. |
| 9.3 | Add PII redaction before any outbound prompt: phone variants, ABHA-like 14-digit identifiers, 12-digit identifiers and emails. Names are never sent. | At least 60 deterministic tests, including preservation of surrounding text and non-PII numbers. |
| 9.4 | Add versioned prompts for extraction, question choice, red-flag second opinion and department classification. Hash and log only prompt version/hash and safe aggregate metadata; never log prompt text. | Prompt safety/format tests; patient text remains delimited data. |
| 9.5 | Extract evidence-backed fields into a restricted patch. Require transcript substring evidence, confidence, supported paths and schema validation; preserve touch-confirmed fields. Run in a bounded background task and keep the deterministic sheet immediately available. | Adversarial validator tests, merge tests, async/failure fallback tests. |
| 9.6 | Let the model select only among up to five approved question IDs. Enforce an enum and the 600 ms decision budget; return the deterministic choice on failure. | Candidate-boundary tests and fallback timing test. |
| 9.7 | Add a parallel red-flag second opinion; combine only with `max(rules, LLM)`. Patient-visible text stays neutral. | Property test proves urgency never decreases; synthetic recall evaluation. |
| 9.8 | Classify only against configured department labels, combine with deterministic routing and require nurse confirmation on disagreement. | Fixed-label tests, agreement/disagreement tests, routing evaluation. |
| 9.9 | Add English/Hindi/Marathi output guards for diagnosis, advice, medicine/dose and URLs. | 100-prompt adversarial evaluation; zero diagnosis/advice leaks. |
| 9.10 | Add per-hospital enable switch, bounded token/output limits, per-session cap, and safe metrics. Cache only explicitly generic fixed text; never cache patient prompts or responses. | Configuration, isolation, cap, metric and cache-key tests. |
| 9.11 | Add 100 extraction dialogues (40 English, 30 Hindi, 30 Marathi), 100 adversarial prompts, deterministic Mock evaluation scripts and Make targets. | CI-safe evaluation output; measured F1, unsupported fields, red-flag recall and routing. |
| 9.12 | Add source/confidence chips and a discreet AI-assisted label to staff case sheets; keep patients unaware of provider choice. | Frontend unit/accessibility checks, locale parity, production build and browser flows. |

## Safety and integration rules

- Deterministic interview, rules-only red flags and rules-only routing remain available on every failure path.
- Patient-controlled content is redacted, wrapped as data, schema-validated and never treated as instructions.
- No free-form question text is accepted; no model output can lower urgency, diagnose, advise, or prescribe.
- The model may fill only allow-listed history fields with a quote found in the normalized source transcript. Touch-confirmed fields win.
- Provider keys remain server-side. Tests and CI use `MockLLM`; live evaluation requires a configured key and must not use real patient data.

## Phase gate

Measure every threshold from Phase 9 in `AGENT_PLAYBOOK.md` and record it in `docs/phase-reports/phase-09.md`. No live metric is inferred from Mock output. If no Gemini key is available, report live quality/latency as not measured and stop at the phase gate for the owner's decision.
