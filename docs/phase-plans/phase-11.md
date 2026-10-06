# Phase 11 implementation plan

## Scope

Implement consent-based queue notifications and tenant-scoped operational analytics, with an admin reports dashboard. Follow the Phase 11 gates in `OPD_Saathi_Agent_Playbook.md` and commit each numbered step after its checks pass.

## Build sequence

1. **11.1 Providers and consent** — add an explicit opt-in field to intake, English/Hindi/Marathi queue templates, and a provider interface with console default plus disabled Twilio SMS and WhatsApp Cloud adapters. Check unit/API/UI coverage and commit.
2. **11.2 Durable notification delivery** — add idempotent per-visit/template persistence, queue position thresholds, retry schedule and dead-letter state, and per-recipient hourly cap. Check duplicate delivery, consent, threshold, retry and rate-cap coverage; commit.
3. **11.3 Analytics** — add a materialized daily department rollup, five-minute worker refresh, tenant-scoped summary/timeseries APIs, audit events, and streamed CSV. Check fixture answers, refresh scheduling and API isolation; measure 100k-row latency/memory where local infrastructure permits; commit.
4. **11.4 Dashboard** — replace the admin reports placeholder with localized stat cards and charts, visible/editable three-minute history baseline, and CSV download. Check TypeScript, frontend tests/build, and axe; commit.
5. **11.5 Phase verification** — run relevant API/frontend checks and gate measurements, document limitations and manual steps in the phase report, update README, then commit.

## Security constraints

- Consent is off by default and stored per visit; no phone means no delivery.
- Never log or store a raw phone number in notification or analytics data. Use the existing encrypted patient field and HMAC digest for rate limits.
- Message text contains only a queue token and queue position, never medical details.
- Scope every analytics query and export to the authenticated hospital.
- External messaging remains disabled unless explicitly configured.

## Known environment limits

The restored `uv.lock` and modified Phase 9 screenshot in the worktree predate this phase and must remain uncommitted. Docker availability and live provider credentials will be checked only if a gate requires them.
