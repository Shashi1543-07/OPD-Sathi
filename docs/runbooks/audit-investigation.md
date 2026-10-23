# Runbook: investigating with the audit log

**Owner:** the hospital's DPO with an administrator · **Used for:** a patient's complaint about
who saw their record, a suspected misuse of an account, an incident (`docs/INCIDENT_RESPONSE.md`)

## What is recorded

Every sign-in (success, failure, lockout, MFA), every view of a case sheet, a patient's history,
a photo of a paper, a report or export, every change to a visit, the queue, consent, settings,
staff accounts and kiosks, with the actor, the time, the hospital and the record. Entries never
contain answers, names or phone numbers. The application cannot change or delete them.
**Reading the audit log is itself recorded** (`audit.viewed`, `audit.exported`).

## Searching

Settings → Audit log (administrators only). Filter by:

- **Record** (a visit id from the case sheet's address, or a staff account) to see everyone who
  touched it;
- **Person** to see everything one staff member did;
- **Action** (for example `case_sheet.read`, `patient_history.read`, `document.photo_viewed`,
  `auth.login.failed`) and a **date range**.

Export the result as CSV for the investigation file; the export is audited.

## Typical questions

| Question | Filter |
|---|---|
| Who saw this patient's record? | Record = the visit (and earlier visits from the history) · Action contains `read` or `viewed` |
| Did this account do anything unusual? | Person = the account · last 30 days; look for actions outside OPD hours or outside their department |
| Was someone guessing passwords? | Action = `auth.login.failed` / `auth.login.locked` · group by account |
| Who changed this patient's urgency? | Record = the visit · Action contains `urgency` |

## Keeping evidence

Export the entries before any change to the account, and attach the CSV to the incident record.
The audit log is retained for the period in the approved retention schedule (task 7.5.6).
