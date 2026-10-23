# Security incident and personal data breach procedure

**Status:** draft for the hospital's counsel, data protection officer (DPO) and IT to adopt
(Phase 7, task 7.5.7). It is an engineering procedure, not legal advice: counsel confirms every
legal duty and time limit below before go-live, and the hospital rehearses it in a tabletop
exercise with hospital IT.

## Roles

| Role | Who (fill in before go-live) | Does |
|---|---|---|
| Incident lead | Hospital IT head | Runs the response, keeps the timeline, decides containment |
| DPO / grievance officer | Named in the patient notice | Decides whether it is a personal data breach; notifies the Data Protection Board and patients |
| CERT-In point of contact | Hospital IT (registered with CERT-In) | Sends the CERT-In report |
| Clinical lead | Medical superintendent or delegate | Patient safety decisions (paper fallback, calling patients back) |
| OPD Saathi team | Second-line support (`docs/DEPLOYMENT.md`, "Support") | Technical investigation, fixes, evidence |

## Time limits to plan around (confirm with counsel)

- **CERT-In** (Directions under section 70B of the IT Act, 28 April 2022): report specified cyber
  incidents **within 6 hours** of noticing them. Keep ICT system logs for **180 days**, in India.
- **DPDP Act 2023 and the DPDP Rules (notified November 2025):** on becoming aware of a personal
  data breach, inform the **Data Protection Board** and **each affected patient** without delay,
  and send the Board a **detailed report within 72 hours** (what happened, the data and people
  affected, the likely consequences, the measures taken, and how patients can protect
  themselves).
- The hospital is the Data Fiduciary and gives these notices; the operator of OPD Saathi (the
  Data Processor) tells the hospital at once (target: within 1 hour of noticing).

## Steps

### 1. Notice and record (first 15 minutes)

Anyone who notices something wrong (an alert, an odd audit entry, a lost kiosk or laptop, a
report from a patient or researcher) tells the incident lead. The lead opens an incident record
with the time it was noticed: **every clock below starts then.**

### 2. Contain (first hour)

Pick what fits; do not destroy evidence (copy logs before restarting anything):

| Situation | Containment |
|---|---|
| A staff account misused | Deactivate it (Settings → Users); this ends its sessions and live screens |
| A kiosk lost or stolen | Settings → Kiosks → Delete; its device secret stops working |
| A token-signing key or data key exposed | Rotate it (`docs/runbooks/key-rotation.md`) |
| AI or SMS provider compromised | Set the hospital's AI switch off; `LLM_ENABLED=false` / `NOTIFICATION_PROVIDER=console` |
| Server compromised | Isolate it from the network; switch the OPD to paper (`docs/runbooks/server-down.md`); restore to clean hardware from a backup taken before the compromise |

### 3. Assess (first 4 hours)

- What data, which patients, which hospital(s), over what period? The audit log
  (Settings → Audit log, and `docs/runbooks/audit-investigation.md`) records every view of a
  case sheet, history, photo and export, with who and when.
- Was it a **personal data breach** (unauthorised processing, accidental disclosure, loss of
  access or of the keys)? The DPO decides.
- Is it a CERT-In reportable incident? Hospital IT decides with the CERT-In list.

### 4. Report

- **CERT-In within 6 hours** of noticing, by the hospital's point of contact, on CERT-In's form.
- **Data Protection Board and affected patients without delay; detailed report to the Board
  within 72 hours.** Patients are told in their language (the visit records their language),
  in plain words: what happened, what it means for them, what the hospital is doing, and who to
  contact.
- The hospital's own escalation (medical superintendent, state health department as required).

### 5. Recover and learn (within 2 weeks)

Fix the cause, restore service, check that alerts would catch it next time, and hold a
blameless review. Update the threat model (`docs/THREAT_MODEL.md`), this procedure and the
runbooks. Keep the incident record, evidence and notices for the period counsel sets.

## Evidence to keep

- The incident record and timeline.
- Copies of the API, worker and web logs for the period (they hold no patient data by design;
  `services/api/tests/security/test_no_pii_in_logs.py` checks this).
- An export of the relevant audit log entries.
- Copies of every notice sent, with times.

## Rehearsal

A tabletop exercise with hospital IT and the DPO before go-live and once a year: walk through a
lost kiosk, a misused staff account and a stolen backup drive. Record what was slow or unclear
and fix it here.
