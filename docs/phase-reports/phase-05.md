# Phase 05 — Queue, token, and live position

**Status:** Phase 5 implementation is on `phase-05-queue`. Unit, static, frontend, and mocked browser gates pass locally. Live PostgreSQL/Redis concurrency, rebuild/recovery, and latency gates remain unmeasured because this checkout has no usable local database credentials or Docker access. Phase 6 has not started.

## 1. In plain words

After a patient confirms the interview summary, OPD Saathi assigns a daily department token and puts the visit in a deterministic waiting order. The kiosk shows a printable QR receipt, a phone page reports queue position and a wait range, and staff can call and move visits through the queue. PostgreSQL stores the durable state; Redis serves the fast queue view and live updates, with an ARQ worker relaying changes and rebuilding that view.

```text
Confirm → Token → Waiting line → Call → Consultation → Complete
                      ↘ QR / live patient status
```

## 2. What was built

- Deterministic urgency/vulnerability score, starvation behavior, the C/B/D/A worked example, consult-duration average, and rounded ETA range.
- IST-date token keys, collision-resistant token labels, database advisory-lock fallback, opaque HMAC-derived patient status tokens, and a SHA-256-only stored lookup value.
- Atomic Redis sorted-set upsert/reprioritize/remove operations, sequenced pub/sub deltas, patient status pub/sub, a transactional queue outbox, ARQ relay on two-second ticks, readiness-aware startup rebuild, and `python -m app.cli queue-rebuild`.
- Confirmation now stores a token and queue event; the temporary route sends age-under-14 visits to `PAED`, other visits to `GENMED`, and danger-tile visits to the temporary urgent category when those departments are available.
- Public status GET and SSE endpoints, two-second status cache, ETA range, visit state machine, staff queue paging/actions/override audit, first-message-authenticated queue WebSocket, and a least-privilege worker database role bootstrap for both fresh and existing Compose volumes.
- Kiosk QR/print receipt, patient SSE reconnect plus five-second polling fallback, and a staff board with call/start/complete/no-show actions.
- Synthetic `make simulate-queue N=50 URGENT=10` driver using the regular kiosk and patient API path.

## 3. Results against the gate

| Gate item | Threshold | Measured | Result |
|---|---:|---:|---|
| Worked order | C, B, D, A | C, B, D, A | Pass |
| Score/property/starvation tests | 25 table cases; 500 generated examples; starvation case | 25 table cases; 500 Hypothesis examples; 46-minute routine outranks new priority | Pass |
| Queue-domain coverage | at least 95% | 100% (105/105 statements) | Pass |
| Redis operation tests | Atomic enqueue, reprioritize, remove, sequence deltas | Unit tests cover each Lua wrapper; real Redis integration is pending | Partial |
| Concurrent allocation | 1,000 unique tokens/positions | Not measured against PostgreSQL + Redis | Pending |
| Flush and rebuild | Same database-derived order | Rebuild code and fake-connection test produce C, B, D, A; real Redis/PostgreSQL check pending | Partial |
| Redis-outage relay | Recovery after Redis returns | Relay retry/recovery covered with a simulated Redis outage; live service recovery pending | Partial |
| Enqueue latency | p95 ≤80 ms | Not measured | Pending |
| Status throughput/latency | 500 req/s, p95 ≤100 ms | Not measured | Pending |
| Realtime delivery | p95 ≤200 ms | Not measured | Pending |
| Browser path | Token card, patient status, staff call | 5 Playwright checks pass: 3 language flows, patient status, staff call | Pass |

The local shell could not reach WSL (`Wsl/Service/CreateInstance/E_ACCESSDENIED`), and the checked-in development database URL fails password authentication. The current environment therefore cannot supply a trustworthy live-service or performance result. No latency or live concurrency claim is inferred from unit tests.

## 4. Commands and checks

Final local regression evidence:

- Full backend suite: **201 passed** (one upstream Starlette/httpx deprecation warning).
- Queue scoring, token, Redis-wrapper, state-transition, and worker unit set: **72 passed**; queue-domain coverage **100% (105/105 statements)**.
- Ruff and strict mypy: passed; mypy covered **45 application source files**.
- Frontend: **16 Vitest tests passed**; TypeScript typecheck, ESLint, and locale parity (218 keys per language across four namespaces) passed.
- Production frontend build passed: JavaScript bundle **403.62 kB** (**128.26 kB gzip**).
- Playwright: **5 passed** across Phase 4 English/Hindi/Marathi flows plus Phase 5 patient status and staff queue paths. Synthetic screenshots are included below.
- Real database migration, token-allocation load, outbox-on-real-Redis recovery, 500 req/s status load, and latency benchmarks remain pending usable service credentials.

## 5. Screenshots and artifacts

- [Patient status page](../screenshots/phase-05/patient-status-en.png)
- [Staff queue board](../screenshots/phase-05/staff-queue-en.png)
- [Phase 5 browser scenarios](../../apps/web/e2e/phase-05.spec.ts)
- [Queue scoring tests](../../services/api/tests/unit/test_queue_scoring.py)
- [Redis operation tests](../../services/api/tests/integration/test_queue_redis.py)

Screenshots and browser fixtures use synthetic tokens and visit data only.

## 6. How to try it manually

Follow the [Phase 5 manual plan](../phase-plans/phase-05.md). Complete one synthetic intake, scan its QR, sign in as a synthetic staff user, call the visit, and watch the public status page update. Run the rebuild command after starting the worker. Check recovery by briefly stopping Redis only after a working local Docker setup is available.

## 7. Known limits

- The department choice and danger-tile urgency are temporary Phase 5 defaults, not clinician-reviewed routing rules. Phase 6 will replace them.
- Phase 4 questions, translations, consent, and danger-tile content remain pending clinician/native-speaker review. This remains a history-collection tool, not a diagnosis system.
- The live PostgreSQL/Redis and performance gates are not passed or waived; they need a usable configured development stack before Phase 5 can close.

## 8. Next phase preview

Phase 6 adds evaluated red-flag matching and reviewed department-routing behavior. Phase 5 stays open until its live concurrency, rebuild/recovery, and performance measurements are recorded.
