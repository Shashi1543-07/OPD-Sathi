# Phase 01 — UI foundation and language selection

**Updated:** 5 October 2026

**Branch:** `phase-01-ui-foundation` (created from CI-green Phase 0 commit `462808d`)

**Status: Complete.** Slices A–D implementation and automated checks passed. The user approved the completed screens and authorized Phase 2.

## Slice A delivered

- Added the F2 color, typography, spacing, radius, shadow, motion, focus, and touch-target tokens in `apps/web/src/styles/tokens.css`.
- Connected the tokens to Tailwind CSS v4 through its Vite plugin and CSS theme variables.
- Added self-hosted variable Inter and Noto Sans Devanagari packages; both font families are emitted as local WOFF2 assets in the production build.
- Added responsive base styles and separate kiosk/staff typography foundations for the planned display sizes.
- Added a script that calculates WCAG contrast ratios for 12 documented foreground/background pairs and fails below 4.5:1.
- Added the 3 px information-color focus outline with its 2 px white separation and reduced-motion defaults.

## Verification

- Contrast check: all 12 pairs pass. Ratios range from 5.30:1 (urgent label) to 15.39:1 (main text on cards).
- Frontend lint, TypeScript check, Vitest (1/1), locale parity, contrast check, and production build passed.
- `npm install` reported 0 vulnerabilities. The JS entry is 98.42 KB gzipped, below the 250 KB Phase 1 budget.
- Production output includes local WOFF2 files for Inter and Noto Sans Devanagari; it makes no font network requests.

## Slice A review

The Slice A shell was visually reviewed from the user's screenshot. It shows the intended calm palette and readable type hierarchy at the supplied viewport. Responsive review at the other planned sizes is still open.

## Slice B delivered

- Added the F3 shared component batch under `apps/web/src/components/ui/`, including native keyboard controls, focus styles, accessible status names, busy/disabled states, reduced-motion-safe transitions, and dialog focus management.
- Split English, Hindi, and Marathi copy into `common`, `kiosk`, `staff`, and `status` namespaces. Hindi and Marathi copy is draft and still needs native-speaker review.
- Added an in-memory Zustand language store and `?lang=xa` pseudo-locale, which decorates English text while leaving interpolation placeholders intact.
- Expanded the locale checker to verify key parity for every namespace.
- Added `/dev/components` only in Vite development builds. It presents component variants and states, language switching across English/Hindi/Marathi, and the pseudo-locale toggle.
- Added component tests for button busy state, keyboard actions, selection state, number entry, language switching, progress semantics, modal focus/escape behavior, and pseudo-locale expansion/interpolation.

## Slice B verification

- TypeScript: passed.
- ESLint: passed.
- Vitest: 10 tests passed across 2 files.
- Locale parity: 73 keys per language across 4 namespaces; passed.
- Prettier: changed Slice B files passed.
- Production build: passed; JS entry is 103.00 KB gzipped, below the 250 KB Phase 1 budget. The dev gallery route is excluded from the production build.

## Slice B review — approved

The user reviewed the component gallery screenshot and approved continuing with Slice C. The all-language, viewport, axe, and overflow matrix is also recorded below.

## Slice C delivered

- Added the shared kiosk header with language and accessibility controls, a 60-second idle warning, and reset after a further 15 seconds.
- Built the S1 welcome screen and static, clearly synthetic previews for S2–S7. No patient or clinical workflow is connected.
- Added the explicit-activation audio prompt, staff navigation and fake login preview, and fictional `/s/demo` patient status page.
- Configured the PWA to precache the app shell and local fonts only. The production build emits the service worker, manifest, icon, and 15 precache entries (757.48 KiB).

## Slice D verification

- Playwright: 94/94 passed. This includes screenshots and axe checks for 9 screens × 3 languages × 3 viewports (81 combinations), 12 pseudo-locale overflow cases, and a keyboard-only staff login walk-through. No axe violations or horizontal clipping were reported; kiosk controls meet the 64 px touch-target rule.
- Screenshots: 81 PNGs saved in `docs/screenshots/phase-01/` for visual review.
- Lighthouse mobile, three runs per route: `/kiosk` performance 97–98, accessibility 100, best practices 96, maximum CLS 0.00002; `/s/demo` performance 98, accessibility 100, best practices 96, maximum CLS 0.00019. All configured thresholds pass.
- Frontend unit tests: 15 passed. ESLint, TypeScript, locale parity (160 keys per language in 4 namespaces), 12 documented contrast pairs, production build, and `npm audit --audit-level=high` passed; audit reported zero vulnerabilities.
- Production JS entry: 119.22 KB gzip, within the 250 KB budget.
- The Lighthouse CI wrapper was replaced by a small runner using the current Lighthouse package after the wrapper introduced high-severity transitive advisories. The same six route/run checks and thresholds are preserved; the resulting dependency audit is clean.

## Final human review

The user reviewed the completed screens and approved moving to Phase 2. The 81 matrix screenshots remain available in `docs/screenshots/phase-01/`. Hindi and Marathi copy still needs native-speaker review before demo approval.
