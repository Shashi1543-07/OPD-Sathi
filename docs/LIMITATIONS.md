# What OPD Saathi does not do, and why

For hospital administrators, clinicians and IT deciding how to use OPD Saathi. Each limit is
either a boundary on purpose (the intended use) or work not done yet; the second kind names the
task that would change it. Written 24 Oct 2026 for version 0.8 (Phase 7, task 7.11.11).

## It does not make clinical decisions

| It does not | Why | Where it is decided |
|---|---|---|
| Diagnose, name a likely disease, or suggest a medicine or dose | The intended use is history-taking and the queue. Anything more is a different product, with a different regulatory classification | `docs/CLINICAL_SAFETY.md` (intended use), task 7.5.11 |
| Check medicines for interactions, doses or allergies | Needs a licensed, maintained knowledge base and clinical review; an AI judgement here is not acceptable | ADR 015 |
| Lower a patient's urgency by itself | Rules can only raise urgency; lowering it needs a staff decision that is recorded | `docs/SAFETY.md` |
| Put AI text in front of a doctor as fact | Every AI output is a labelled draft citing the answers it rests on, which the doctor accepts, edits or rejects. AI never writes the doctor's notes | Hazard H5 in `docs/CLINICAL_SAFETY.md`; task 7.11.1 |
| Trust a value read from a photo of an old paper | What AI reads is a draft until a doctor compares it with the photo; results are flagged only against the range printed on the same paper | Hazard H6 |

The red-flag rules and the triage score help staff notice a patient sooner. They are not a
triage system that replaces a nurse, and they miss things (hazard H1): staff stay responsible
for who is seen first.

## It knows only what the patient said

- The history is the patient's own account, in their words, translated. It is not verified.
  "Don't know" and "prefer not to say" stay open questions, never denials.
- When a parent, guardian or helper answers, the record says so, but the history is still
  second-hand (hazard H11).
- A patient joins today's visit to earlier ones with their OPD card (number and PIN), or a
  nurse links them after checking age and sex. A phone number alone never identifies anyone.

## Languages and people

- Three languages: English, Hindi and Marathi. Adding one needs the question bank, the screens,
  the notices and the recorded prompts in that language, each reviewed (task 7.11.10).
- All clinical wording and translations are drafts until clinicians and native speakers sign
  them off (`docs/CLINICIAN_REVIEW.md`). Production refuses to start with unsigned content
  when `CLINICAL_CONTENT_APPROVAL_REQUIRED` is set.
- Voice works best in a quiet place. In a crowded OPD a handset, tapping, or a nurse helping
  may be needed (task 7.12.3).
- Screen-reader and keyboard use have been checked with automated scans, not yet with TalkBack
  and NVDA users (task 7.12.2).

## Patients

- Patients do not see their own records in OPD Saathi; a printed card with a PIN is not
  strong enough sign-in for that (ADR 014). They see their place in the queue, can stop AI help
  and messages, and keep their OPD card.
- SMS and WhatsApp messages are built but not sent until the hospital has a provider contract
  and DLT registration (task 7.11.6).

## Systems and data

- It is not connected to ABDM or to the hospital's HIS yet. A FHIR export is the next step
  (task 7.10.1); ABDM certification is months of external process (tasks 7.10.2, 7.10.3).
- It runs on one server per hospital, inside the hospital network, over HTTPS. It is not built
  to face the internet, and Kubernetes is not supported.
- The kiosk's offline short form covers a lost network, not a stopped server: then the paper
  pack is used and entered afterwards (`docs/paper/`).
- If the queue store (Redis) is lost, the queue is rebuilt from PostgreSQL; interviews in
  progress restart (`docs/runbooks/redis-loss.md`).
- Photos of papers are kept in PostgreSQL, encrypted; storage grows with use and is planned in
  the server sizing (task 7.8.8).
- Without an AI provider, or when a patient says no, everything still works on the server
  alone: the rules, tapping and typing, recorded question audio. Follow-up questions,
  translation of free text, the doctor's draft brief and reading photos are what is lost.

## Before real patients

OPD Saathi 0.x is for pilots with synthetic data and staging. Version 1.0 waits for the
go-live checklist (`docs/GO_LIVE_CHECKLIST.md`): clinical and language sign-off, the
penetration test, signed notices and contracts, the usability test and staff training.
