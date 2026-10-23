# Contributing to OPD Saathi

OPD Saathi collects patients' histories and runs the queue in government hospital OPDs. A
mistake can delay a sick patient or expose their health information, so every change follows the
rules below. `AGENT_PLAYBOOK.md` describes how the product was built; `docs/DECISIONS.md` records
why. New frameworks, databases or major libraries need an ADR and the owner's approval first
(ADR 001).

## Every change

1. Work on a branch; open a pull request into `main`.
2. CI must pass: lint, types, unit and integration tests (95% coverage on core and domain),
   web unit and end-to-end tests, evaluations, security scans and the production smoke test.
3. One approving review is required; `.github/CODEOWNERS` adds the required reviewers for
   clinical content and security-sensitive code.
4. Use synthetic data only. Never put real patient data, `.env`, keys, database dumps or
   unredacted logs in the repository, an issue or a pull request.
5. Logs must never contain answers, names, phone numbers, tokens or codes. The log-capture test
   fails if they do (`services/api/tests/security/test_no_pii_in_logs.py`).

## Clinical change control

These files decide what patients are asked, what counts as a danger sign, how urgency is
weighed and which department a patient goes to:

- `services/api/app/domain/redflags/rules.yaml`
- `services/api/app/domain/triage/weights.yaml`
- `services/api/app/domain/interview/bank/` (questions, modules, triggers, danger tiles)
- `services/api/app/domain/interview/consent/` (consent notices)
- `services/api/app/domain/routing/engine.py`

A change to any of them needs, in the same pull request:

1. **A clinician's approval** as a review from the clinical reviewers in `CODEOWNERS` (a vaidya
   for the Ayurveda module), and for patient-facing text a native speaker's review of each
   language.
2. **A passing evaluation** (`make eval`), with the golden sets updated for the change and the
   reason for every changed expectation written in the pull request.
3. **An updated review record**: the row in `docs/CLINICIAN_REVIEW.md` with the reviewer's name,
   the date and the new content version. Content marked `approved` must carry that record; the
   loaders refuse it otherwise.
4. **A hazard log check**: if the change affects a hazard in `docs/CLINICAL_SAFETY.md`, update
   its controls and residual risk.

A clinical change ships switched off where a switch exists, and is announced in the release
notes in plain language for the clinical lead.

## Database migrations

Releases can be rolled back by one version, so migrations follow **expand, migrate, contract**:

1. **Expand** (release N): add new columns, tables or values as optional; the code writes both
   the old and the new shape.
2. **Migrate** (release N or N+1): backfill in batches; long backfills are rehearsed on a copy
   with a million rows and run outside OPD hours.
3. **Contract** (release N+1 or later): remove the old shape only after a release that no longer
   reads it.

Every migration has a working `downgrade()`; CI runs upgrade, downgrade to base and upgrade
again. A migration that cannot be reversed without losing data says so in its docstring, and the
release notes tell the operator to take a fresh backup first (the upgrade script always does).
Row-level security is lifted only inside the migration, only on the tables it touches, and put
back before it ends.

## Security issues

Report vulnerabilities privately (`SECURITY.md`), never in a public issue.
