# Phase 01 — UI foundation and language selection

**Status: In progress; implementation and automated gates for Slices A–D are complete.** Phase 0 is complete (CI green on `462808d`) and implementation is on branch `phase-01-ui-foundation`. The component gallery review was approved; final human visual review of the completed screens is the remaining Phase 1 gate.

## Goal

Give OPD Saathi a calm, consistent visual foundation that works for first-time patients across Hindi, Marathi, and English. Deliver a polished language-selection screen, reusable accessible components, and fake-data previews of the later patient and staff experiences. No API calls or clinical workflow logic are part of this phase.

## Starting point in this repository

- `apps/web` is a React/TypeScript/Vite shell with placeholder routes for `/kiosk`, `/staff`, `/s/:token`, and `/dev/components`.
- `src/styles/tokens.css` is currently only a placeholder, and `src/styles.css` contains basic shell styling.
- Locale seed files currently contain only the common namespace. Zustand and `vite-plugin-pwa` are already dependencies.
- Tailwind, self-hosted Fontsource fonts, Playwright, axe, and Lighthouse CI are not yet configured. Add only the packages needed for this phase and commit the lockfile.

## Scope and boundaries

- Build the Saathi UI tokens and components from Parts F1–F9 of the playbook.
- Keep all visible copy in `common`, `kiosk`, `staff`, or `status` locale namespaces for `en`, `hi`, and `mr`. Include an `xa` pseudo-locale that expands text by about 40% for layout checks.
- Use synthetic content only. S2–S7 and `/s/demo` are static previews, not working registration or clinical flows.
- Do not connect to the backend, add diagnosis/advice, add live audio assets, or cache patient data. `AudioPrompt` is a no-op when its local audio file is absent.
- Keep provider integrations, real login, persistence of patient information, and functional intake for their later phases.

## Implementation sequence

### Slice A — visual foundation (playbook 1.1)

- Define the exact color, typography, spacing, radius, shadow, motion, and focus tokens from F2 in `src/styles/tokens.css` and the Tailwind theme.
- Add and self-host Inter and Noto Sans Devanagari through Fontsource packages; no runtime font requests to external services.
- Set kiosk/staff typography and responsive layouts for 1280×800 kiosk landscape, 768×1024 tablet portrait, 390×844 phone, and staff desktop from 1366×768.
- Add a contrast script that checks every documented foreground/background pairing and fails below 4.5:1.

### Slice B — components, translations, and gallery (1.2–1.4)

- Implement the first component batch from F3 under `src/components/ui/`: Button, LanguageTile, DangerTile, ChoiceButton, ChipGroup, NumberPad, FacesScale, ProgressDots, UrgencyBadge, ConfidenceChip, HelpButton, ReadAloudButton stub, ConnectionIndicator, OfflineBanner, IdleWarningDialog, ConfirmDialog, Toast, Skeleton, EmptyState, ErrorState, and StatCard.
- Give interactive components visible focus, keyboard behavior, disabled/busy states where relevant, and accessible names. Use CSS transitions only and respect reduced motion.
- Split locale JSON into `common`, `kiosk`, `staff`, and `status`; add a session-only Zustand language store and the `?lang=xa` pseudo-locale.
- Extend the locale checker to ensure all English keys exist in Hindi and Marathi. Add Vitest component/state/keyboard coverage.
- Make `/dev/components` development-only and show the component states in all three supported languages.

### Slice C — patient/staff shells and previews (1.5–1.8)

- Build the kiosk header with hospital name, language selector, help, text-size, and high-contrast controls.
- Implement the 60-second idle warning and reset 15 seconds later; clear session-only state when the idle reset fires.
- Add `AudioPrompt`, which plays an existing `/audio/{lang}/{id}.mp3` asset after explicit user activation and silently does nothing when it is absent.
- Fully style S1 welcome/language selection. Add static, clearly fake-data previews at `/kiosk/preview/s2` through `/s7`.
- Build the staff navigation shell and keyboard-usable login preview, plus a mock `/s/demo` patient status page.
- Configure PWA precaching for the app shell and fonts only; do not cache user/session content.

### Slice D — automated and visual quality gate (1.9–1.10)

- Add Playwright coverage for screenshots of every screen in 3 languages × 3 target viewports, axe scans, pseudo-locale overflow, kiosk tap targets, and a keyboard walk-through of staff login.
- Configure Lighthouse CI for kiosk and status pages using the playbook thresholds.
- Save review screenshots to `docs/screenshots/` and record actual gate results in the Phase 1 report.
- Ask for human visual review of `/dev/components`, `/kiosk`, `/kiosk/preview/s2`–`s7`, staff login keyboard flow, phone layout, and 200% zoom before closing the phase.

## Files likely to change

- `apps/web/package.json` and `package-lock.json` for Tailwind, font and UI test tooling.
- `apps/web/src/styles/tokens.css`, `src/styles.css`, Tailwind/Vite/PWA configuration, and the app router.
- New UI components, kiosk/staff/status page modules, language state, and locale namespace files under `apps/web/src/`.
- Locale/contrast scripts, component tests, Playwright configuration and specs, Lighthouse CI config, and CI workflow steps.
- `docs/screenshots/`, `docs/phase-reports/phase-01.md`, README, and this project TODO after implementation and measurement.

## Acceptance gate

- Axe: zero serious or critical violations on every screen.
- Every color pair used for text meets 4.5:1 contrast.
- Every kiosk interactive target is at least 64×64 px; staff controls remain keyboard usable.
- Lighthouse mobile: Performance ≥85, Accessibility ≥95, Best Practices ≥95; CLS <0.1; PWA installable.
- Pseudo-locale text does not clip; all locale keys are present.
- Kiosk initial JavaScript is ≤250 KB gzipped.
- Human visual review is complete, and screenshots/results are saved in the Phase 1 report.

## Dependencies and risks

- Phase 0 approval and the new `phase-01-ui-foundation` branch are prerequisites.
- Playwright browsers and fonts add install size; configure CI caching and keep the kiosk bundle within the stated budget.
- Hindi/Marathi translations need native-speaker review before demo approval. Keep draft strings marked for review; do not invent clinical language.
- High-contrast and text-size controls must preserve layout and minimum target sizes across all target viewports.

## First implementation session after approval

1. Completed: verify a clean worktree and Phase 0 approval; create/check out `phase-01-ui-foundation`.
2. Completed: re-read the detailed F1–F9 requirements and inspect the current app shell.
3. Completed: implement Slice A, including the contrast script, and run frontend checks.
4. Completed: user visually reviewed the Slice A shell and approved moving to Slice B.
5. Completed: implement Slice B, translations, session language state, pseudo-locale, and development component gallery; run its checks.
6. Completed: user reviewed `/dev/components` and approved continuing with Slice C.
7. Completed: implement Slices C and D, run the screen matrix, audit, Lighthouse checks, and save review screenshots.
8. Current: request final visual review of the completed kiosk previews, staff login, patient status, phone layout, and 200% zoom before Phase 2.
