# End-to-end scenarios (Phase 5)

The fifteen scenarios from section 10 of `IITB_READINESS_PLAN.md`, what the system must do in
each, and the tests that show it. All tests use synthetic data. API tests run against real
PostgreSQL and Redis (`services/api/tests/integration`); web tests are Vitest component tests
(`apps/web/src/**/*.test.tsx`) and Playwright specs (`apps/web/e2e`, API mocked).

Status: **15 of 15 covered.** Scenarios 5, 10, 12 and 14 found defects, now fixed (see
"Defects found" below). Scenario 14 covers both uploads: the prescription scan and, since
Phase 6, photos of old papers kept for the doctor (R6).

| # | Scenario | What must happen | Tests |
|---|---|---|---|
| 1 | First-time patient on a phone | The phone completes the same interview as a kiosk over plain-HTTP Wi-Fi, gets a token and a live status page; the board marks the visit "Phone" | API `test_mobile_checkin.py::test_phone_checkin_reaches_the_queue_like_a_kiosk_visit`; e2e `mobile-checkin.spec.ts` "a patient checks in on their own phone over plain HTTP and keeps their token" |
| 2 | Returning patient with a previous visit | The OPD number and PIN link today's visit to the record (age and sex must fit); the doctor of the treating department sees earlier visits; staff can link a patient who forgot the card | API `test_returning_patients.py` (all six tests); e2e `mobile-checkin.spec.ts` "a returning patient joins today's visit to their record with their OPD card" |
| 3 | Incomplete interview | A patient who walks away is never queued: the kiosk's idle reset ends the visit with no token, it stays off the board and the register, and any danger sign they tapped still alerts a nurse. A phone that closes the page resumes the same visit at the same question | API `test_scenarios.py::test_scenario_03_an_unfinished_interview_never_reaches_the_queue`; e2e `kiosk-back-and-quit.spec.ts` "Stop and exit cancels the visit …", `mobile-checkin.spec.ts` "a refresh in the middle of the interview resumes the same visit" |
| 4 | Red-flag symptom | A danger sign or a red-flag answer makes the visit urgent at once, alerts staff over the live board within a second, and stays marked "Nurse to confirm" until a nurse acknowledges it | API `test_queue_workflow.py::test_a_tapped_danger_sign_is_marked_until_a_nurse_confirms_it`, `test_redflag_alert_delivery.py::test_staff_websocket_alert_delivery_p95_under_one_second`; unit `test_redflags.py`, `test_triage_score.py`; evaluation `make eval-redflags`; e2e `triage-why.spec.ts` |
| 5 | Duplicate submission on a slow network | A double tap or a retry never creates a second answer, visit or token | API `test_scenarios.py::test_scenarios_05_and_10_an_answer_sent_twice_is_saved_once`, `test_reliability_fixes.py::test_simultaneous_confirmations_issue_one_token`, `::test_confirmation_retry_returns_the_same_token_after_lost_session_state`, `::test_a_correction_retried_after_a_lost_session_write_is_saved_again`, `test_offline_intake.py::test_offline_replay_survives_api_restart_without_duplicate_visit`; every staff queue action takes an `Idempotency-Key` |
| 6 | Called patient does not appear | After the grace period (3 minutes by default) staff mark them absent; they keep token, urgency and arrival time; their status page sends them to the desk; the next patient is called | API `test_queue_workflow.py::test_a_patient_who_misses_their_call_is_recalled_to_their_place`; e2e `queue-recall.spec.ts` (both tests) |
| 7 | Skip and recall | Recall puts the patient back on their original arrival time; only a skipped patient can become a no-show | API `test_queue_workflow.py::test_a_patient_who_misses_their_call_is_recalled_to_their_place`, `::test_only_a_missed_patient_becomes_a_no_show`; unit `test_queue_state.py` |
| 8 | Patient returns after being marked absent | Recalled to their place while the visit is open; once closed as a no-show they register again and their OPD card joins the new visit to the same record, where the doctor sees the missed visit | API `test_queue_workflow.py::test_a_patient_who_misses_their_call_is_recalled_to_their_place`, `test_scenarios.py::test_scenario_08_a_patient_who_left_registers_again_with_their_card` |
| 9 | AI provider unavailable | Check-in, triage, routing and the token work without the AI service; the doctor gets the rule-based case sheet with no AI draft; photo reading says "say or type it"; a hanging AI service adds at most about 5 seconds to confirmation | API `test_scenarios.py::test_scenario_09_check_in_works_while_the_ai_service_is_down`, `::test_scenario_09_a_hanging_ai_service_only_slows_confirmation_a_little`; unit `test_intake_ai_upgrade.py::test_summarize_interview_falls_back_quietly`, `test_llm_provider.py` (timeouts, circuit breaker) |
| 10 | Network interruption | An answer whose reply was lost can be sent again safely and the kiosk says "tap again" in the patient's language; a kiosk with no network keeps an encrypted short form and syncs it once; the status page and the staff board reconnect by themselves, renewing an expired staff sign-in | API `test_scenarios.py::test_scenarios_05_and_10_an_answer_sent_twice_is_saved_once`, `test_offline_intake.py`; web `KioskIntake.test.tsx` "asks the patient to tap again when the connection drops while answering", `StaffQueueBoard.test.tsx` "renews an expired sign-in before reconnecting the live board"; e2e `phase-12-offline.spec.ts` |
| 11 | Two doctors in conflict | Two doctors pressing "Call next" together get different patients; a doctor holds one patient at a time and cannot act on another doctor's patient | API `test_queue_workflow.py::test_two_doctors_calling_at_once_never_get_the_same_patient`, `::test_a_doctor_has_one_patient_at_a_time`; web `StaffQueueBoard.test.tsx` (DOCTOR_BUSY message) |
| 12 | Another patient's records | A patient session reaches only its own visit, on every intake route and the audio socket; staff of another department get "not found"; a session cannot cancel a visit that has a token or change answers after confirming | API `test_access_isolation.py::test_a_patient_session_reaches_only_its_own_visit`, `::test_another_department_cannot_reach_a_visit`, `test_session_safety.py` (first two tests), `test_returning_patients.py::test_history_stays_with_the_treating_department_and_inside_the_hospital` |
| 13 | Another hospital's records | Every route that names a record answers "not found" to another hospital's staff, changes nothing and writes nothing to the owner's audit trail; lists, lookups, the audit log and the live board show none of another hospital's patients | API `test_access_isolation.py` (all tests, including the route inventory), `test_tenant_rls.py`, `test_phase3_data.py::test_domain_rls_and_encrypted_patient_fields`, `test_mobile_checkin.py::test_phone_checkin_codes_never_cross_hospitals`, `test_analytics_rollup.py` |
| 14 | Invalid or oversized document | Only a JPEG, PNG or WebP photo whose contents match its type, under 1 MB, reaches the model or is kept; anything else is refused with a reason the kiosk explains in the patient's language; at most 5 readings per visit in 10 minutes; at most 5 kept papers per check-in (10 per visit with staff) | API `test_scenarios.py::test_scenario_14_only_real_photos_of_a_sensible_size_are_read`, `test_documents.py::test_only_real_photos_of_known_kinds_are_kept` and `::test_a_patient_takes_photos_back_until_confirming_and_adds_at_most_five`; web `KioskIntake.test.tsx` "says why a prescription photo could not be used", `KioskPapers.test.tsx` "explains a refused photo in the patient's language" |
| 15 | Simultaneous registrations | Many kiosks and phones confirming at once get distinct tokens in order with no gap, and the board shows every patient once | API `test_scenarios.py::test_scenario_15_simultaneous_registrations_get_distinct_tokens_in_order` (12 at once); load smoke `tests/perf/test_load_smoke.py` (40 patients on the running stack, results below) |

## Defects found by the scenarios and the isolation tests

Each was reproduced by a test that failed before the fix.

| Defect | Effect | Fix |
|---|---|---|
| An answer re-sent after its reply was lost (new request id) was refused with `422 "answer does not match the current question"`, shown to the patient in English | On flaky Wi-Fi the patient was stuck on a question the server had already moved past | The server recognises an answer it has already saved and returns where the interview stands; answers to one session are handled one at a time, so a double tap cannot store two |
| `DELETE /intake/sessions/{id}` set the visit to `cancelled` whatever its state, with no timeline entry or board update | A kiosk idle reset after a lost confirmation reply, or anyone holding the phone's session, could take a queued, called or finished patient out of the queue | Only a visit without a token can be ended this way (409 otherwise); the end is recorded in the visit timeline and the audit log; ownership is checked before anything else |
| Answer corrections after confirmation were blocked only by the Redis session phase | If the confirmation's session update was lost, a correction reopened the interview and set a queued visit back to `registering` | Corrections, Back and new answers are refused once the database records a token |
| A file of any kind sent as `image/png` reached the model | A PDF or other file was sent to the AI provider as a "photo" | The file's signature must match its declared type; an explicit 1 MB limit; checks run before the AI provider is asked |
| "Call staff" had no limit and worked after the visit had finished | A script holding a phone session could keep the staff board ringing | 5 presses per visit in 10 minutes; refused once the visit is done or a no-show |
| After confirmation the Redis session kept every answer, the status link and the card PIN for two hours | Patient answers in plain text in Redis long after they were needed (R12) | The session keeps only what ending it or calling staff needs; follow-up drafts are deleted; a repeated confirmation rebuilds the receipt from the database |
| An open staff board kept receiving alerts after its sign-in expired or its account was deactivated | A deactivated nurse's open screen kept showing patients' red-flag words | The board re-checks the account every minute and closes at sign-in expiry (4401); the board renews its sign-in and reconnects |
| `DELETE /intake/sessions/{id}` answered another hospital's session id with 410 instead of 404 | Told a caller that a session existed | Ownership is checked before the session is read |

## Load smoke

`services/api/tests/perf/test_load_smoke.py` runs against the running stack (skipped
elsewhere), from the repository root so it uses the stack's `.env`:

```
make load-smoke                                          # http://127.0.0.1:5173/api
make load-smoke LOAD_SMOKE_URL=http://127.0.0.1:8080/api # when start-demo.ps1 chose 8080
```

`LOAD_SMOKE_PATIENTS`, `LOAD_SMOKE_ARRIVAL_SECONDS` and `LOAD_SMOKE_P95_MS` change the size of the
rush and the latency limit.

It creates a synthetic hospital with AI switched off, sends 40 patients through the whole
check-in with arrivals spread over 10 seconds while four staff screens refresh the queue and
alerts every 2 seconds and every patient opens their status page, then removes the hospital.
It fails on any failed request, a missing or repeated token, or a step whose 95th-percentile
latency exceeds 1.5 seconds. Results are recorded in the Phase 5 row of the plan's checklist.
