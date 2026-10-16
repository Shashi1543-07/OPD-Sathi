# Queue policy: who is called next, missed calls and recall

Status: implemented (Phase 3, migration `0019_queue_recall`). The rules below are the software's
behaviour, not an approved clinical policy. The hospital's clinical and administrative leads
must review the items under [Decisions for the hospital](#decisions-for-the-hospital) before
real patients use the system. Triage weights are marked `draft_needs_clinician_review`.

## 1. Who is called next

1. **Urgent patients first, always.** An urgent patient is never behind a routine or priority
   patient, however long the others have waited.
2. **Then by virtual arrival time:** arrival time minus a head start. The head start comes from
   the explainable triage score (0.6 minutes per point), bounded by the level: routine patients
   get less than 18 minutes, priority patients at least 18 and at most 45. A nurse's change of
   level therefore always wins over the points.
3. **Ties** are broken by arrival time, then by visit id, so the order is deterministic.

The server alone computes urgency and position. A patient cannot change their answers after
confirming their summary, cannot see anyone else's position, and their phone or the kiosk never
sends a score.

**Self-reported danger signs.** A patient who taps a danger sign (chest pain, breathing
difficulty, heavy bleeding and so on) is placed first as urgent, and the nurse is alerted at
once. Their row on the queue board stays marked **Nurse to confirm** until a nurse acknowledges
the alert after seeing the patient. If the nurse finds it is not urgent, they change the level
with a written reason (recorded in the audit log). Urgency never drops without a recorded staff
decision. The system does not penalise inconsistent answers; it routes them to a person.

**AI suggestions** are checks, not decisions: an AI "urgent" counts as priority at most until a
nurse confirms it, and it is always shown as an AI suggestion with the quoted words.

## 2. Calling, missed calls and recall

```
queued ──call──► called ──start──► in_consult ──complete──► done
  ▲                │  │
  │   put back     │  └──mark absent (after the grace period)──► skipped
  └────────────────┘                                              │  │
  ▲                                                               │  └──patient left──► no_show
  └──────────────────────────────recall───────────────────────────┘
```

- **Call.** A doctor's call reserves the patient for that doctor. A nurse can call a patient to
  the desk without reserving them. **Call next** lets the server choose (section 1), so two
  doctors pressing it together get two different patients.
- **One patient per doctor.** A doctor can have at most one patient called or in consultation.
  To call another, they first start, finish, mark absent or put back the current one. A doctor
  cannot start or act on a patient another doctor has called; a nurse or administrator can mark
  that patient absent or put them back.
- **Grace period.** A called patient can be marked absent only after the hospital's grace period
  (default 3 minutes, 0–15 allowed, set by an administrator under Settings → Departments →
  Calling patients). The server enforces it; the screens show a countdown on **Mark absent**.
- **Mark absent** (`skipped`). The patient keeps their token, encounter, urgency and arrival
  time. Their status page asks them to go to the registration desk. They appear under **Missed
  calls** on the queue board and on the doctor's patient list, and any open alert for them stays
  visible to nurses.
- **Recall.** When the patient comes back, any staff member in the department recalls them. They
  return to the queue on their original arrival time, so they are normally seen next within
  their priority class. The number of missed calls is recorded and shown; nothing is decided
  automatically from it.
- **Put back.** A call made by mistake returns the patient to the queue unchanged, with no missed
  call on record.
- **No-show.** Only a skipped patient can become a no-show: staff mark it when the patient has
  left, or the end-of-day close-out does it.

### End of day

Visits left open on an earlier day are closed automatically, and each gets a timeline entry with
the reason `day_closed`:

| Open state | Becomes | Why |
|---|---|---|
| Waiting (`queued`) | `cancelled` | Not seen because the OPD closed; not the patient's absence |
| Called, or marked absent | `no_show` | Called and never seen |
| In consultation | `done` | The consultation was not closed on screen |
| Registration never finished | `cancelled` | No token was issued |

## 3. Who can do what

All actions are limited to the staff member's own department; a visit in another department is
reported as not found. Administrators act across the hospital.

| Action | Doctor | Nurse | Administrator |
|---|---|---|---|
| Call a patient, call next | Yes (reserves the patient) | Yes (no reservation) | Yes |
| Start, complete a consultation | Yes, unless another doctor has the patient | No | Yes |
| Mark absent, put back | Yes, unless another doctor has the patient | Yes | Yes |
| Recall, mark no-show | Yes | Yes | Yes |
| Cancel a visit | No | Yes | Yes |
| Change priority (with a reason) | No | Yes | Yes |
| Set the grace period | No | No | Yes |

## 4. Records

- **Visit timeline** (`visit_events`): every queue change with its previous and new state, who
  made it (staff member, the patient, or the system) and when. The application can add and read
  entries but never change or delete them. Doctors see it on the consultation page; it is
  available to staff of the visit's department (`GET /visits/{id}/queue-events`).
- **Audit log**: `queue.called`, `queue.in_consult`, `queue.done`, `queue.skipped`,
  `queue.recalled`, `queue.returned`, `queue.no_show`, `queue.cancelled`, `queue.override` and
  `admin.queue_settings.updated`, each with the staff member and the previous state.

## 5. Guarantees and how they are tested

| Guarantee | Mechanism | Test |
|---|---|---|
| Two doctors calling at once never get the same patient | Calls in a department take one transaction lock; the visit row is locked before it changes | `test_two_doctors_calling_at_once_never_get_the_same_patient` |
| A doctor holds one patient at a time | Per-doctor lock and check before every call and start | `test_a_doctor_has_one_patient_at_a_time` |
| A missed patient keeps their place | Recall restores the original arrival time | `test_a_patient_who_misses_their_call_is_recalled_to_their_place` |
| Queue actions stay inside the department and the hospital | Department check on every transition, call-next, override and timeline read; row-level security per hospital | `test_queue_actions_stay_inside_the_staff_members_department` |
| No-show only after a missed call; day close-out labels | State machine; close-out mapping | `test_only_a_missed_patient_becomes_a_no_show`, `test_end_of_day_cancels_waiting_visits_and_closes_missed_calls` |
| A tapped danger sign is marked until a nurse confirms it | Unacknowledged danger-tile alert on the visit | `test_a_tapped_danger_sign_is_marked_until_a_nurse_confirms_it` |
| A double tap does not repeat an action | `Idempotency-Key` on every queue action | Existing idempotency tests |

Integration tests are in `services/api/tests/integration/test_queue_workflow.py`; the state
machine is unit-tested in `tests/unit/test_queue_state.py`; screens are covered by
`StaffQueueBoard.test.tsx`, `DoctorConsultation.test.tsx` and `e2e/queue-recall.spec.ts`.

## Decisions for the hospital

These need sign-off by the hospital's clinical and OPD administration leads:

1. **Grace period:** 3 minutes by default. Busy OPDs may want longer; the setting allows 0–15.
2. **Where a recalled patient goes:** back to their original place (seen next within their
   priority class). The alternative is the end of the line; changing it is a small code change.
3. **Waiting patients at closing time** are recorded as cancelled, not no-shows.
4. **Nurses calling and marking absent:** allowed, without reserving the patient for a doctor.
5. **Head-start weights** in `services/api/app/domain/triage/weights.yaml` (draft).
