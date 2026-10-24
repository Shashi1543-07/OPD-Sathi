# Clinical safety case and hazard log

**Phase 7, task 7.6.3.** Status: **draft for the clinical lead's review**. No Indian standard
mandates a hazard log for this kind of software; this one follows the practice of DCB0129
(clinical risk management for health IT manufacturers) because it is a clear, reviewable way to
show that each foreseeable way of harming a patient has been thought about, controlled, and
owned.

## Intended use (the boundary every hazard is judged against)

OPD Saathi **collects a patient's history in their own words and runs the OPD queue.** It does
not diagnose, does not name a likely disease, does not suggest medicines or doses, and does not
give treatment advice (`docs/SAFETY.md`). It **can only make a patient more urgent**: urgency
never drops without a recorded staff decision. Every AI output is a labelled draft that a
clinician accepts, edits or rejects. Whether its triage and red-flag functions make it software
as a medical device under the Medical Devices Rules, 2017 is assessed separately (task 7.5.11).

## Risk scoring

**Severity:** 1 minor (inconvenience) · 2 significant (delay without harm) · 3 considerable
(delayed care that could cause harm) · 4 major (serious harm) · 5 catastrophic (death).
**Likelihood:** 1 very low · 2 low · 3 medium · 4 high · 5 very high.
**Risk = severity × likelihood:** 1–4 acceptable · 5–9 acceptable only with the controls listed
and a named owner · 10+ not acceptable for go-live.

Initial risk is before the controls; residual risk is with them, as built. Likelihoods are
engineering estimates until the pilot (task 7.6.5) measures them.

## Hazards

### H1 · A danger sign is missed (false negative)
- **Cause:** the patient does not tap a danger tile; a red-flag rule does not match their words
  (dialect, spelling, mixed language); voice transcription drops a word; the patient skips the
  question.
- **Effect:** a very unwell patient waits in the routine queue.
- **Initial risk:** severity 5 × likelihood 3 = **15**
- **Controls:** danger tiles shown before any question, in three languages and as icons, with
  audio; 15 deterministic red-flag rules matching English, Hindi, Marathi and Hinglish terms
  (evaluation: 100% recall on the synthetic set, `docs/EVALUATION.md`); AI second opinion can
  only add caution; the patient confirms a read-back of their answers; nurse triage at the desk
  remains; kiosk sign "If you feel very unwell, tell the staff now" (7.15.3).
- **Residual risk:** 5 × 2 = **10**. Not acceptable until the clinical review of rules and tiles
  (7.6.1) and the pilot's measured sensitivity against nurse triage (7.6.5) bring the likelihood
  to 1. **Owner:** clinical lead.

### H2 · An urgent alert is not acted on
- **Cause:** the nurse is busy or away; the board is not watched; the alert sound is muted.
- **Effect:** a flagged patient still waits.
- **Initial risk:** 5 × 3 = **15**
- **Controls:** urgent patients go first in the queue automatically; the board re-alerts every
  60 s until acknowledged; the row stays "Nurse to confirm"; alerts escalate beyond the board to
  a second device or a supervisor after the agreed time (7.6.7); alert for any urgent alert
  unacknowledged for 5 minutes (7.9.2).
- **Residual risk:** 5 × 2 = **10** until escalation (7.6.7) is built and drilled; then 5 × 1.
  **Owner:** OPD nursing in-charge.

### H3 · The wrong department
- **Cause:** routing rules map words to the wrong department; the patient describes the problem
  vaguely; a department is closed.
- **Effect:** delay while the patient is re-routed.
- **Initial risk:** 3 × 3 = **9**
- **Controls:** General Medicine is the default; ambiguous cases go to nurse triage; staff can
  move a patient; urgency travels with the patient. Routing evaluation in `docs/EVALUATION.md`.
- **Residual risk:** 2 × 2 = **4**. **Owner:** clinical lead (routing review, 7.6.1).

### H4 · The wrong patient's record is linked
- **Cause:** a patient enters someone else's OPD card (family member); staff link the wrong
  record at the desk.
- **Effect:** the doctor reads another person's history and acts on it.
- **Initial risk:** 4 × 2 = **8**
- **Controls:** card link needs number and PIN and today's age and sex must fit the record;
  limited attempts; staff linking is audited and shown on the doctor's view; phone numbers never
  link records; history shows dates and departments so a mismatch is visible.
- **Residual risk:** 4 × 1 = **4**. **Owner:** medical records.

### H5 · The doctor reads an AI sentence the patient never said
- **Cause:** the language model invents, mistranslates or over-interprets.
- **Effect:** a decision based on a false history.
- **Initial risk:** 4 × 3 = **12**
- **Controls:** the AI draft is labelled, each statement cites the questions it came from and
  shows the patient's own words beside it; schema and semantic validation; output guard rejects
  diagnoses, drugs and doses; the doctor accepts, edits or rejects; the rule-based case sheet
  with verbatim answers is always available; weekly AI-quality review with criteria for switching
  AI off (7.6.6); AI off by default until a contract exists (7.5.9).
- **Residual risk:** 4 × 2 = **8**, acceptable with the controls and owner.
  **Owner:** clinical lead.

### H6 · A misread laboratory value on an old paper
- **Cause:** AI reads a value, unit or range wrongly from a photo.
- **Effect:** a wrong value is believed.
- **Initial risk:** 4 × 3 = **12**
- **Controls:** readings are drafts beside the photo; flags come from arithmetic against the
  range printed on the same paper, never a general range; values that do not parse are not
  flagged; only accepted readings appear in summaries and timelines (7.11.3).
- **Residual risk:** 4 × 2 = **8**, acceptable with the controls. **Owner:** clinical lead.

### H7 · A stale queue (the board or status page shows the wrong order)
- **Cause:** the worker stops; Redis is lost; a network partition.
- **Effect:** patients called out of order; an urgent patient not at the top.
- **Initial risk:** 3 × 3 = **9**
- **Controls:** PostgreSQL is the source of truth and the queue is rebuilt from it; worker
  health check and restart; outbox-age alert (7.9.2); live boards reconnect and show "reconnecting";
  "Call next" is decided by the server from the database.
- **Residual risk:** 3 × 1 = **3**. **Owner:** hospital IT.

### H8 · A kiosk shows someone else's answers
- **Cause:** a session not ended; a back button; a shared browser tab.
- **Effect:** disclosure of health information; the next patient answers on top.
- **Initial risk:** 3 × 2 = **6**
- **Controls:** tab-scoped session; ends on confirm, quit and idle timeout; nothing from earlier
  visits shown on a kiosk; e2e tests of back and quit (`kiosk-back-and-quit.spec.ts`).
- **Residual risk:** 3 × 1 = **3**. **Owner:** engineering.

### H9 · The system is down during OPD hours
- **Cause:** server, power, disk, database or network failure.
- **Effect:** check-in stops; patients wait without a token; urgent patients not flagged.
- **Initial risk:** 4 × 3 = **12**
- **Controls:** paper fallback pack and rehearsal (`docs/runbooks/server-down.md`, 7.7.7); the
  desk asks the six danger-sign questions on paper; UPS; backups and restore drills; standby
  (7.7.6); alerts to a named person within 5 minutes (7.9.2).
- **Residual risk:** 4 × 1 = **4** once the paper fallback is rehearsed. **Owner:** OPD in-charge.

### H10 · Questions or notices are misunderstood in translation
- **Cause:** a Hindi or Marathi question means something different; a patient with low literacy
  cannot read a notice.
- **Effect:** wrong answers; consent not truly informed.
- **Initial risk:** 3 × 3 = **9**
- **Controls:** every text reviewed by a native speaker (7.6.2); notices read aloud; plain
  language review (7.12.4); usability test across literacy levels (7.12.1); nurse-assisted
  registration for anyone who needs help.
- **Residual risk:** 3 × 2 = **6** until the language review is signed. **Owner:** clinical lead.

### H11 · Someone answers for a patient who cannot
- **Cause:** a child, an unconscious or confused adult; a companion answers.
- **Effect:** history from the wrong person, recorded as the patient's own.
- **Initial risk:** 3 × 3 = **9**
- **Controls:** "Who is answering?" asked with the consent and again, as a required choice, for
  every patient under 18; recorded on the visit and each consent; shown on the doctor's screen
  and the case sheet PDF (7.5.3, 7.11.7, built 24 Oct 2026); nurse-assisted path.
- **Residual risk:** 3 × 2 = **6**. Open: counsel's note on guardians' consent (7.5.3).
  **Owner:** clinical lead.

## Review record

| Date | Reviewer | Role | Outcome |
|---|---|---|---|
| 22 Oct 2026 | Engineering | Draft | First version |
| | | Clinical lead | |

The hazard log is reviewed after the pilot, after any clinical content change that affects a
hazard (`CONTRIBUTING.md`, "Clinical change control"), and at least once a year.
