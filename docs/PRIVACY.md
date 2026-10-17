# Privacy and data handling

This document describes the local MVP data model. It is an engineering inventory, not a legal compliance assessment. Use synthetic data until the clinic's privacy officer and qualified counsel approve the notices, retention periods, processors, and deployment.

## Data map

| Data | Where it is stored | Protection and use |
|---|---|---|
| Patient name, phone, ABHA identifier | PostgreSQL `patients` | Encrypted at application level; phone lookup uses a keyed digest. The digest is not unique, because family members often share one phone, and a phone number never links records on its own. Kiosk short-form intake does not collect contact details. |
| OPD number | PostgreSQL `patients.mrn`; yearly counter in `patient_number_counters` | Assigned when a patient's first visit is confirmed (format `26-000123`). Tenant-scoped. It identifies the patient's record within one hospital only. |
| OPD card PIN | Not stored | Six digits derived from the HMAC key, the patient ID and `patients.card_version`. Printed on the token slip and shown on the done screen. Anyone holding the number and PIN can link a new visit to the record, so the slip should be kept like a hospital card. |
| Age, sex, preferred language | PostgreSQL `patients`; age is also copied to `visits` | Tenant-scoped PostgreSQL row-level security. Used for interview context and queue prioritization, and for the identity check when an OPD card is used. |
| Visit links | PostgreSQL `visits.patient_link`, `visits.previous_visit_id` | Records whether a visit was joined to an earlier record by the patient's card or by staff, and which earlier visit the patient said today's visit follows. |
| Consent by purpose: version, text hash, language, answer, time, channel, withdrawal | PostgreSQL `consents` (one row per purpose) | `care` (required) and `ai` (online AI help, optional), each with the version and SHA-256 of the notice the patient heard, so a "no" is recorded as precisely as a "yes". The channel is `kiosk`, `mobile` (patient's own phone) or `nurse` (staff-assisted). A withdrawal records when and whether the patient or staff recorded it. Tenant-scoped; every answer and withdrawal is also in the audit log. |
| Phone check-in session and ticket | Browser `localStorage` on the patient's own phone | The patient access token, until the end of the day, and today's token number, status link, OPD number and PIN. The ticket is removed the first time the page opens on a later day. Kiosks keep the session in tab-scoped storage only. |
| Interview answers and transcripts | PostgreSQL `interview_turns`; Redis intake session while the patient answers | Free text can contain sensitive health information. Turns are encrypted at application level (AES-GCM, bound to the visit) and tenant-scoped. The Redis session holds the answers only until the patient confirms the summary; from then on it keeps only the visit, kiosk, channel and language for the rest of its two hours, and the AI follow-up drafts are deleted. A repeated confirmation rebuilds the receipt (token, status link, OPD card) from the database. Do not include answers in logs or cache snapshots. |
| Structured clinical summary | PostgreSQL `case_sheets` | Encrypted at application level (AES-GCM, bound to the visit); the column holds only ciphertext, and only the repository decrypts it. Tenant-scoped. |
| Queue and urgency events | PostgreSQL `visits`, `red_flag_events`, and transactional `outbox`; Redis state | Tenant-scoped in PostgreSQL. Redis carries short-lived queue/session state. |
| Visit timeline | PostgreSQL `visit_events` | Every queue change (called, marked absent, recalled, completed) with the staff member or "patient"/"system" and the time. Tenant-scoped and append-only for the application role. Shown only to staff of the visit's department. No clinical content. See `docs/QUEUE_POLICY.md`. |
| Audit events | PostgreSQL `audit_log` | Append-only application role, tenant-scoped. Metadata must not contain patient answers or credentials. |
| Notification delivery status | PostgreSQL `notifications` | Does not store the destination phone number; provider delivery remains disabled by default. |
| Offline kiosk form | Browser IndexedDB | AES-GCM encrypted at rest with a non-extractable key stored in the same browser database. Records are removed after confirmed sync. Device access controls and browser-profile protection remain necessary. |
| Uploaded documents | PostgreSQL `documents` metadata schema exists | Upload and object storage are not enabled in the current offline phase. |

## Consent by purpose

Each check-in asks two separate questions, each with its own short notice that is shown and read aloud in the patient's language:

1. **Care (required):** the history is collected and shared with the clinic team for this visit. Declining ends the check-in and sends the patient to the desk.
2. **Online AI help (optional):** online services (Google Gemini, Sarvam AI) may turn the patient's voice into text, translate the answers, read photos of their old papers and prepare a draft for the doctor. Nothing is pre-selected, and "No" continues the visit exactly as "Yes" does.

With no "yes" (declined, withdrawn, or never asked), the visit runs on this server alone, and the API enforces it, not the screen: no language-model call (follow-up questions, translation, the doctor's draft, second opinions, department suggestion, photo reading), no speech recognition by an online service or by the browser's own recogniser, and the read-back of the patient's own words only with a voice installed on the device. Recorded question audio is the hospital's text and is played either way. The patient taps and types, and the doctor gets the rule-based case sheet with the answers in the patient's own words. The hospital administrator's AI switch can turn language-model features off for everyone; it can never turn them on for a patient who said no.

A "yes" can be changed until the summary is confirmed, and withdrawn afterwards: by the patient with **Stop AI help** on their status page (the status link can only stop AI help, never start it, so a photographed token slip cannot switch it on), or by staff at the desk. From then on nothing more about the visit is sent to AI services, work already running is not saved, the doctor no longer sees the AI draft (also on earlier-visit summaries and the PDF), and the draft cannot be reviewed. Processing done before the withdrawal is not undone.

## Returning patients and record linking

Every check-in starts a new patient record. A visit joins an existing record in only two ways, and both are audited:

- **The patient enters their OPD number and PIN** at the kiosk or on their phone. The API also checks that today's age and sex agree with the record (birth year within one year; when both the record and today's answer say female or male, they must match). A wrong number, wrong PIN or mismatch is refused without saying which part failed. Attempts are limited to 5 per visit in 15 minutes and 10 per OPD number per hour. The audit entry records only the outcome, never the number or PIN. The kiosk or phone shows the date of the last visit so the patient can mark a follow-up; it never shows what an earlier visit was about.
- **A nurse or admin links the visit at the desk** after confirming who the patient is in person. They can search by OPD number or phone number. The results show OPD number, age, sex, visit count and last visit date, with no names or clinical details. Staff can link only visits in their own department. Both the search (`patient.lookup`) and the link (`queue.patient_linked`) are audited, and the doctor's view shows that staff made the link.

When a visit joins an existing record, the record created for it at check-in is emptied: phone, birth year and sex are removed and its OPD number is retired. Linking is refused if that record already has other visits. There is no tool for merging two records that both have history; that needs a separate, approved process.

Doctors and nurses can read a patient's earlier visits (`GET /visits/{id}/history`) only through a visit in their own department that is still open or took place today; once it is closed and in the past, the API refuses with `HISTORY_CLOSED`. Admins can read it through any visit. Each read is audited as `patient_history.read`. The history shows up to 20 earlier visits at the same hospital: date, department, token number, complaint, red flags and the AI draft brief, plus allergies, medications and conditions with the date each was last reported. Records are never shared between hospitals.

Not built yet: re-issuing a lost OPD card (changing `card_version` invalidates the old PIN, but there is no staff screen for it), patient access to their own history, and ABHA-based linking.

## Retention configuration and current enforcement

`.env.example` includes `RETENTION_DAYS=30` and `ANONYMISE_DAYS=90` as defaults for `services/api/scripts/retention.py`. The command requires one `--hospital-id`, runs as a tenant-scoped dry run by default, and requires `--apply` before it deletes interview turns older than the retention cutoff, removes old summaries/red-flag events, and pseudonymizes direct patient identifiers when all of that patient's visits are older than the anonymization cutoff. A PostgreSQL integration test verifies both dry-run and apply behavior on a synthetic tenant.

The command is not scheduled, and retained visits, consent/audit history, and stable patient UUIDs remain. This is pseudonymization, not guaranteed anonymization. Until the clinic approves the schedule and an operator runs the command under an audited process, the configured values do not cause automatic deletion. Do not claim the defaults satisfy a statutory or clinical record-retention requirement.

Backup directories contain encrypted records and Redis state snapshots, are created with owner-only permissions, and are excluded from Git. The restore script refuses the configured database name, checks SHA-256 manifests, and compares the restored visit count. A checksum and visit-count comparison do not prove a complete restore; Redis restore and broader row-count verification remain outstanding.

## Access, transfer, and secrets

- PostgreSQL uses per-hospital row-level security for tenant tables. Application encryption keys and HMAC keys must remain stable and must be backed up separately from database snapshots.
- Every API route that names a record (a visit, an alert, a staff account, a department, a kiosk or a patient session) answers "not found" to staff of another hospital, staff of another department and other patients' sessions, and changes nothing. `services/api/tests/integration/test_access_isolation.py` checks each such route and fails when a new one is added without a check.
- A patient's kiosk or phone session can change its visit only until the token is issued: it cannot cancel a queued visit, change confirmed answers, or call staff once the visit has finished. A staff screen's live connection closes when the sign-in expires or the account is deactivated.
- Redis state uses AOF persistence and contains queue, session, rate-limit, and idempotency data. It must be treated as sensitive. The separate Redis cache is bounded and evictable.
- External LLM, speech, SMS, and WhatsApp providers are optional and must remain disabled until the clinic approves the data flow, notices, contracts, and geographic processing region. Even when enabled, language-model and online speech processing of a patient's data happens only with that patient's consent to online AI help (see "Consent by purpose").
- Do not put `.env`, credentials, patient data, database dumps, Redis snapshots, or unredacted logs in source control or issue trackers.

## Review items before real patient data

- Have the clinic approve the consent copy and all English, Hindi, and Marathi clinical text with native speakers and clinicians.
- Decide the lawful purpose and retention schedule for transcripts, case sheets, queue records, audit events, and backups.
- Review field-level encryption for clinical JSON and Redis persistence/encryption at rest for the actual deployment.
- Document processor agreements, data-subject request handling, breach response, key rotation, backup expiry, and restore access.
- Perform a deployment-specific privacy and security assessment with qualified counsel; this file is not a DPDP Act compliance opinion.
