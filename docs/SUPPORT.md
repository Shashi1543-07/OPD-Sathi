# Support model

For the hospital and the OPD Saathi team to agree before go-live (Phase 7, task 7.9.6). Fill
the blanks, sign at the end, and keep one copy with the go-live checklist and one on the wall
of the server room.

## Who does what

| Line | Who | Handles | How to reach |
|---|---|---|---|
| First line | The OPD desk lead (in OPD hours) and the hospital's IT person | Kiosk off or frozen, a screen not loading, a forgotten password or lost phone (Settings → Users), the paper fallback (`docs/runbooks/server-down.md`), every alert first (`docs/runbooks/alerts.md`) | Desk: ________ · IT: ________ |
| Second line | The OPD Saathi team | Anything the runbooks do not resolve within their stated time; restores; upgrades; security issues | One channel: ________ (phone and email) |
| Clinical | The hospital's clinical lead | Questions about questions, urgency or routing; approving clinical changes (`CONTRIBUTING.md`) | ________ |
| Privacy | The hospital's data protection officer or grievance officer | Patient requests and complaints about their data (`docs/PRIVACY.md`); breaches (`docs/INCIDENT_RESPONSE.md`) | ________ |

## Response times (second line)

| Severity | Example | Answer within | Working on it within |
|---|---|---|---|
| 1 — OPD stopped | Server down, nobody can check in, data may be lost | 15 minutes, any time | 1 hour |
| 2 — OPD slowed | Alerts or queue not updating, a kiosk group down, backups failing | 1 hour in OPD hours | 4 hours |
| 3 — Inconvenience | One screen wrong, a report figure doubted | 1 working day | 5 working days |
| Security | Suspected breach or vulnerability | 1 hour, any time | At once (`docs/INCIDENT_RESPONSE.md`) |

Severity 1 starts the paper fallback at once, without waiting for an answer.

## Every call is recorded

Date and time, who called, what happened, severity, what was done, when it was resolved, and
whether a runbook covered it. Patient details are never put in the record: a token or a request
id (shown on every error) is enough to investigate.

## Every month

The second line sends the hospital a one-page report: availability in OPD hours, alerts and
calls by severity, backups and the last restore drill, kiosks offline, upgrades made, and open
issues. The clinical lead and IT review it; recurring problems become fixes or runbook changes.

## Sign-off

| Role | Name | Signature | Date |
|---|---|---|---|
| Medical superintendent or clinical lead | | | |
| Hospital IT | | | |
| OPD Saathi team | | | |
