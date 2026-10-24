# Go-live checklist

**Phase 7, section 10 and task 7.16.4.** No real patient uses OPD Saathi at a hospital until every
line below is ticked with its evidence attached and the sign-off table at the end is signed.
Make one copy of this file per hospital installation and keep it with the hospital's records.

Hospital: ______________________ Installation host: ______________________ Version: __________

## Gates and engineering evidence

| ☐ | Item | Evidence to attach |
|---|---|---|
| ☐ | Gates G0–G3 of the Phase 7 plan passed | Links to the merged pull requests and green CI runs |
| ☐ | The production overlay is in use; the stack refuses development values | `scripts/prod_smoke.sh` passing in CI on the released version; `docker compose ... config` output |
| ☐ | Image versions and digests recorded | `docker compose ... images` output; release notes |
| ☐ | HTTPS on every device; certificate renewal and expiry alert tested | `scripts/check-tls.sh <host>` output; renewal rehearsal note (`docs/runbooks/certificate.md`) |
| ☐ | Administrators use two-step sign-in | Settings → Users shows "Two-step" for every administrator |
| ☐ | No demo accounts or demo hospital on the server | `SELECT name FROM hospitals` shows only this hospital |
| ☐ | Penetration test closed by a CERT-In-empanelled auditor (7.4.6) | Report and re-test letter |
| ☐ | Image scan, DAST and fuzzing clean or waived in writing | Latest CI and nightly-security runs; `.trivyignore` and `infra/zap/zap-rules.tsv` with reasons |
| ☐ | Point-in-time backups, encrypted and off-site; restore drill in the last 90 days | Backup report; signed drill log (`docs/runbooks/restore.md`) |
| ☐ | Keys escrowed in two places | Key register entry (`docs/runbooks/key-escrow.md`) |
| ☐ | Alerts reach a named person within 5 minutes; runbooks in place | Alert drill log; `docs/runbooks/` |
| ☐ | Support contacts on the wall of the server room; support agreement signed | Photo; signed `docs/SUPPORT.md` |
| ☐ | Load and soak targets met for this hospital's size (7.8) | Load test report |
| ☐ | Clock synchronised to NIC/NPL time | `timedatectl` output |
| ☐ | UPS with automatic clean shutdown tested | Test note (`docs/runbooks/power.md`) |

## Clinical, privacy and legal

| ☐ | Item | Evidence to attach |
|---|---|---|
| ☐ | Every clinical content item signed off (7.6.1); the Ayurveda module by a vaidya, or switched off | `docs/CLINICIAN_REVIEW.md` with names, dates and versions; `python -m app.cli content-status` shows 0 awaiting sign-off; `CLINICAL_CONTENT_APPROVAL_REQUIRED=true` in `.env` |
| ☐ | Every Hindi and Marathi text reviewed by a native speaker (7.6.2) | Review record per file |
| ☐ | Hazard log reviewed by the clinical lead (7.6.3) | `docs/CLINICAL_SAFETY.md` review record |
| ☐ | Patient notice and consent texts approved by counsel in three languages (7.5.2); the grievance officer's contact set (Settings → Departments → Privacy notice) and the notice poster up | Counsel's approval; `app/domain/privacy/notice-*.md` and the consent files marked approved with the reviewers' names (`python -m app.cli content-status`) |
| ☐ | Processor agreement between hospital and operator signed (7.5.1) | Agreement |
| ☐ | AI and SMS provider contracts signed, or AI and SMS off by configuration (7.5.9, 7.11.6) | Contracts, or `.env` showing `LLM_ENABLED=false` and `NOTIFICATION_PROVIDER=console` |
| ☐ | Retention schedule approved; the retention job scheduled; its first report reviewed (7.5.6) | Schedule; report |
| ☐ | Breach and incident procedure adopted; tabletop exercise held (7.5.7) | `docs/INCIDENT_RESPONSE.md` with roles filled; exercise notes |
| ☐ | Regulatory classification assessed (7.5.11) | Counsel's note |
| ☐ | Urgent-alert escalation agreed and drilled (7.6.7) | Drill log |

## People and the room

| ☐ | Item | Evidence to attach |
|---|---|---|
| ☐ | Nurses, doctors and administrators trained (7.15.4) | Attendance and competency checks |
| ☐ | Patient signs up: how to check in, privacy notice QR, "If you feel very unwell, tell the staff now" (7.15.3) | Photos |
| ☐ | Paper fallback pack at every OPD desk; rehearsed (7.7.7) | Photo; rehearsal note (`docs/runbooks/server-down.md`) |
| ☐ | Kiosks placed, locked down, with privacy filters (7.3.9, 7.12.3) | Placement checklist |
| ☐ | Pilot results meet the thresholds agreed before the pilot (7.6.5) | Pilot report |
| ☐ | `v1.0.0` tagged and signed, with SBOM and release notes (7.14.2) | Release page |

## Sign-off

| Role | Name | Signature | Date |
|---|---|---|---|
| Medical superintendent or clinical lead | | | |
| Hospital IT | | | |
| Data protection officer or legal | | | |
| OPD Saathi team | | | |
