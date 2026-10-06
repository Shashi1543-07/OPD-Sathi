# Privacy and data handling

This document describes the local MVP data model. It is an engineering inventory, not a legal compliance assessment. Use synthetic data until the clinic's privacy officer and qualified counsel approve the notices, retention periods, processors, and deployment.

## Data map

| Data | Where it is stored | Protection and use |
|---|---|---|
| Patient name, phone, ABHA identifier | PostgreSQL `patients` | Encrypted at application level; phone lookup uses a keyed digest. Kiosk short-form intake does not collect contact details. |
| Age, sex, preferred language | PostgreSQL `patients`; age is also copied to `visits` | Tenant-scoped PostgreSQL row-level security. Used for interview context and queue prioritization. |
| Consent version, language, grant time, channel | PostgreSQL `consents` | Tenant-scoped audit of the notice accepted for an intake. |
| Interview answers and transcripts | PostgreSQL `interview_turns`; Redis intake session | Free text can contain sensitive health information. Database access is tenant-scoped; Redis state is durable in the state service. Do not include it in logs or cache snapshots. |
| Structured clinical summary | PostgreSQL `case_sheets` JSON | Tenant-scoped but currently stored as JSON rather than field-level encrypted. Restrict database access and use synthetic data until the privacy review approves this storage design. |
| Queue and urgency events | PostgreSQL `visits`, `red_flag_events`, and transactional `outbox`; Redis state | Tenant-scoped in PostgreSQL. Redis carries short-lived queue/session state. |
| Audit events | PostgreSQL `audit_log` | Append-only application role, tenant-scoped. Metadata must not contain patient answers or credentials. |
| Notification delivery status | PostgreSQL `notifications` | Does not store the destination phone number; provider delivery remains disabled by default. |
| Offline kiosk form | Browser IndexedDB | AES-GCM encrypted at rest with a non-extractable key stored in the same browser database. Records are removed after confirmed sync. Device access controls and browser-profile protection remain necessary. |
| Uploaded documents | PostgreSQL `documents` metadata schema exists | Upload and object storage are not enabled in the current offline phase. |

## Retention configuration and current enforcement

`.env.example` includes `RETENTION_DAYS=30` and `ANONYMISE_DAYS=90` as defaults for `services/api/scripts/retention.py`. The command requires one `--hospital-id`, runs as a tenant-scoped dry run by default, and requires `--apply` before it deletes interview turns older than the retention cutoff, removes old summaries/red-flag events, and pseudonymizes direct patient identifiers when all of that patient's visits are older than the anonymization cutoff. A PostgreSQL integration test verifies both dry-run and apply behavior on a synthetic tenant.

The command is not scheduled, and retained visits, consent/audit history, and stable patient UUIDs remain. This is pseudonymization, not guaranteed anonymization. Until the clinic approves the schedule and an operator runs the command under an audited process, the configured values do not cause automatic deletion. Do not claim the defaults satisfy a statutory or clinical record-retention requirement.

Backup directories contain encrypted records and Redis state snapshots, are created with owner-only permissions, and are excluded from Git. The restore script refuses the configured database name, checks SHA-256 manifests, and compares the restored visit count. A checksum and visit-count comparison do not prove a complete restore; Redis restore and broader row-count verification remain outstanding.

## Access, transfer, and secrets

- PostgreSQL uses per-hospital row-level security for tenant tables. Application encryption keys and HMAC keys must remain stable and must be backed up separately from database snapshots.
- Redis state uses AOF persistence and contains queue, session, rate-limit, and idempotency data. It must be treated as sensitive. The separate Redis cache is bounded and evictable.
- External LLM, speech, SMS, and WhatsApp providers are optional and must remain disabled until the clinic approves the data flow, notices, contracts, and geographic processing region.
- Do not put `.env`, credentials, patient data, database dumps, Redis snapshots, or unredacted logs in source control or issue trackers.

## Review items before real patient data

- Have the clinic approve the consent copy and all English, Hindi, and Marathi clinical text with native speakers and clinicians.
- Decide the lawful purpose and retention schedule for transcripts, case sheets, queue records, audit events, and backups.
- Review field-level encryption for clinical JSON and Redis persistence/encryption at rest for the actual deployment.
- Document processor agreements, data-subject request handling, breach response, key rotation, backup expiry, and restore access.
- Perform a deployment-specific privacy and security assessment with qualified counsel; this file is not a DPDP Act compliance opinion.
