# Staff speed: clicks for the commonest actions

Phase 7, task 7.12.6. How many clicks (or key presses) the actions staff repeat all day take,
counted on the screens as they are, with what changed in G3. A click here is one press of a
mouse or finger; typing is listed separately. The usability test with real staff (task
7.12.1) measures time and errors on the same actions; this table is what it starts from.

## Nurse (Queue)

| Action | Clicks | How |
|---|---|---|
| Acknowledge an urgent alert | 1 | The alert at the top of the queue → Acknowledge (also by keyboard: Tab, Enter) |
| Call the next patient | 1 | Next to call → Call next (Tab, Enter) |
| Mark a called patient absent | 1 | The patient's row, once the countdown ends → Mark absent |
| Recall a patient who comes back | 1 | Missed calls → Recall |
| Change a patient's priority | 3 + a reason | ⋯ → choose the level → type the reason → Apply override |
| Confirm a suggested department | 1 + a reason | Type the reason → Confirm department |
| Re-issue a lost OPD card | 3 + a search | Patients → Lost OPD card → search → Re-issue card → Yes, re-issue |

## Doctor

| Action | Clicks | How |
|---|---|---|
| Call the next patient | 1 | Queue → Call next, or the case sheet's Call patient |
| Start the consultation and see the case sheet | **1** (was 2) | Queue → Start consult now opens the case sheet; before G3 the board stayed open and Open case sheet was a second click. From the case sheet itself: Start consultation |
| Accept an AI draft | 1 | Accept (Edit or Reject are one click too); checking a source is one tap per sentence |
| Write and save notes | 1 + typing | Clinical notes → type → Save notes (the tab is one more click when another is open) |
| Print or save the case sheet | 1 key, or 2 clicks | P, or ⋯ → Print / PDF |
| Finish the consultation | 1 | Finish consultation |
| Mark a called patient absent | 1 on the case sheet, 2 on the queue | Mark absent next to Start consultation; on the queue ⋯ → Mark absent |
| Put a called patient back in the queue | 2 | ⋯ → Put back in queue |

A whole visit for a doctor, from calling the patient to finishing with notes and a printed
sheet: **6 clicks and 1 key press** plus the notes (Call next, Start consult, Accept, Clinical
notes, Save notes, Finish consultation, P), down from 7 clicks.

## Administrator (Settings)

| Action | Clicks | How |
|---|---|---|
| Add a staff account | 3 + the form | Settings → Users → fill in → Create staff account |
| Reset a password, unlock, sign out everywhere | 2–3 | Users → the person's ⋯ → the action → confirm |
| Register a kiosk | 3 + a label | Settings → Kiosks → label → Register kiosk; open the setup link once on the kiosk |
| Check the system | 1 | System (database, Redis, worker, last backup, disk) |

## Keyboard

| Where | Key | Does |
|---|---|---|
| Case sheet | N | Back to the queue |
| Case sheet | E | Edit the first history field |
| Case sheet | P | Print the case sheet |
| Case sheet tabs | ← → Home End | Move between tabs |
| Everywhere | Tab, Shift+Tab, Enter, Space | Every control is reachable and visible when focused; the queue's alert and Call next are tested with the keyboard alone (`apps/web/e2e/queue-languages-keyboard.spec.ts`) |

The shortcuts do nothing while typing in a field, and none of them changes a patient's state:
a stray key press can never call, start or finish a consultation.

## Next, from the usability test

Candidates to measure with real staff before changing anything: a nurse's priority change (3
clicks and a reason), and whether doctors want the next patient offered on Finish
consultation.
