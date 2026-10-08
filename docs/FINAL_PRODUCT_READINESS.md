# OPD Saathi — final product readiness review

**Review date:** 7 October 2026  
**Scope:** Phase 13/14 handoff, product and UI audit, isolated synthetic validation.  
**Data used:** fictional clinic and synthetic patient records only.

## 1. Phase 14 completion

Phase 14 implementation and automated acceptance are complete. Demo-safe providers, repeatable synthetic seeding, Judge Mode, localized safety framing, staff explainer, branding slot, first-paint splash, screenshots, and updated operating documentation are delivered. The full Phase 14 implementation and evidence are recorded in [phase-14.md](phase-reports/phase-14.md).

## 2. Product-wide audit

The audit traced F1–F15 and service health/recovery from backend capability to intended role, route, visible UI, and key states. The full matrix and role notes are in [PRODUCT_AUDIT.md](PRODUCT_AUDIT.md). The patient path, nurse/reception queue, doctor consultation, and admin analytics/configuration flows are represented in the interface. The doctor lands on the live queue; there is no separate doctor dashboard page. Prescription storage and messaging still require deployment-specific adapters/configuration.

## 3. Backend-to-frontend traceability

Core mappings include consent and identity to kiosk intake and assisted registration; red flags/routing/queue state to staff alert and token screens; case-sheet/version APIs to doctor/nurse review; analytics/CSV to admin Reports; offline outbox to short-form kiosk intake; and auth/audit to staff sign-in and admin controls. Rows F1–F15, service health, states, and known limitations are listed in the product audit.

## 4. UX improvements made

- Localized the Judge Mode safety ribbon in English, Hindi, and Marathi.
- Localized the staff “How it works” explainer and preserved `?demo=1` across staff navigation.
- Removed the admin placeholder landing and routed admin Judge Mode to Reports; staff defaults to Queue.
- Made the kiosk activation entry visibly usable, added an optional hospital logo URL, and added a first-paint splash.
- Split intake, queue, consultation, reports, admin settings, patient status, offline intake, and development-only screens into route chunks. Production entry JavaScript decreased from 576.91 kB to 427.86 kB minified (174.16 kB to 135.36 kB gzip).
- Saved screenshots across patient, nurse/reception, doctor, and admin paths under `docs/screenshots/phase-14/`.

## 5. Visual evidence

The Phase 14 directory contains three localized kiosk welcome images and representative synthetic patient-summary, nurse-queue/consent, doctor-consultation/case-sheet, and admin-kiosk/analytics captures. The kiosk images show the offline banner because they were generated before connecting the isolated API; the nurse queue image captures a transient reconnect state and an old synthetic fixture. These are audit evidence, not all polished demo frames. The screenshots cover representative role states, not every workflow state at every viewport. E2E overwrites of pre-existing phase screenshot paths were restored from a pre-run copy.

## 6. Measured accessibility

- Axe found zero serious or critical findings in the audited nurse consent, doctor case-sheet, admin kiosk, role-access, and analytics screens.
- All 12 documented color pairs passed the project's WCAG AA 4.5:1 contrast gate; measured ratios ranged from 5.30:1 to 15.39:1.
- Lighthouse accessibility scored 100 on all six production-preview runs across `/kiosk` and `/s/demo`.
- Language and viewport E2E exercised 3 locales, kiosk landscape, tablet portrait, and phone; pseudo-locale checks found no clipped page in tested screens.

Automated accessibility checks do not replace keyboard/screen-reader testing with assistive technology or user research.

## 7. Multilingual validation

English, Hindi, and Marathi have matching locale keys: 407 keys per language across four namespaces. The E2E suite covers localized patient and staff surfaces, and Judge Mode safety copy is translated. Native-speaker review for naturalness and clinical meaning is still required; key parity alone does not prove translation quality.

## 8. Regression and runtime evidence

| Check | Result |
|---|---|
| API | 761 passed; 82% aggregate coverage; one Starlette/httpx deprecation warning |
| Frontend unit | 37 passed across 12 files |
| Playwright E2E | 116 passed sequentially on the isolated synthetic stack |
| Large queue | 5,000 rows; live urgent update 71.90 ms (<100 ms gate) |
| Lint, TypeScript, locale parity, contrast | All passed |
| Production frontend build | Passed after route splitting; 427.86 kB entry JS (135.36 kB gzip), below Vite's 500 kB advisory; operational screens are separate chunks |
| Lighthouse | Six production-preview runs passed configured thresholds after route splitting; accessibility 100, kiosk performance 88–89, demo performance 96, CLS 0.000–0.067 |
| Synthetic evaluations | Interview 30/30; routing test 96/96; red flags 60/60 must-catch and 50/50 negation; extraction 100 fixtures with 0 unsupported fields; adversarial 100/100 blocked; urgency monotonicity 500/500 |
| API smoke | `/healthz` 200; `/readyz` 200 |
| k6 smoke | 1,387/1,387 checks, 0% errors, max 25 VUs, 19.74 requests/s, p95 5.12 ms |
| Dependency/source security | npm audit: 0 vulnerabilities; both Python lockfiles: no known vulnerabilities; Bandit passes; Gitleaks: no leaks across 82 commits after four exact synthetic test-fixture fingerprints were documented in `.gitleaksignore` |

The k6 case is health-only. Synthetic evaluation uses deterministic/mock fixtures and does not measure clinical accuracy or live model quality. Lighthouse performance varies between runs; one initial kiosk sample scored 84, while the six-run repeat passed the configured minimum of 85.

## 9. Remaining gaps

- Human acceptance: run the full demo twice without internet and have a first-time user complete intake without coaching.
- Clinician and native-speaker review of medical rules, patient questions, consent, and Hindi/Marathi copy.
- Production deployment definition, production-container smoke, provider/storage/messaging configuration, and deployment validation.
- Broader Phase 13 checks remain: complete security scanners, representative API/queue/socket load and soak testing, query/scale validation, clinic-approved retention policy/scheduling, and expanded telemetry.
- In-app browser inspection could not be performed because the browser bridge returned `missing field sandboxPolicy`. The Playwright runs and saved screenshots are supporting evidence, not a claim that in-app manual inspection passed.
- Semgrep auto-rule execution stalled before producing scan results; Trivy image scanning, ZAP, and Schemathesis are still not complete.
- Representative kiosk/queue/status/socket k6 workloads, the 30-minute soak, database query-count/scale checks, and expanded telemetry remain open. The passing k6 result above covers only `/healthz`.
- A production deployment definition, target-specific storage/messaging/provider configuration, and production-container smoke remain open. No target deployment platform was specified, so those are not implemented as a guess.

## 10. v1.0 readiness recommendation

**Do not tag or describe this build as v1.0 production-ready yet.** Phase 14 is complete for implementation and automated acceptance, but the human offline/usability gates, clinical/language sign-off, production deployment smoke, and broader Phase 13 release checks remain open. Continue with a controlled synthetic demo/review build until those gates have owners and passing evidence.
