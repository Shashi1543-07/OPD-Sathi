# AI use disclosure

OPD Saathi was built with AI coding assistants. This file says which tools were used, for what,
and what the team did. It applies to everything in this repository.

**In short: about 80–90% of the code is AI-assisted. The team did the problem research, the
design, the testing, the review and the demo.**

## Tools used to write the software

| Tool | Made by | What it did |
|---|---|---|
| OpenAI Codex | OpenAI | Coding agent: wrote and changed application code, tests, database migrations and scripts from tasks the team set, and ran the tests |
| Claude (Claude Code) | Anthropic | Coding agent: the same kinds of changes, plus code review, documentation drafts and pull requests |

Most of the documentation in [`docs/`](docs/), including the guide PDFs, was also drafted with
these tools and then checked and corrected by the team.

## AI services the running app uses

These are not authoring tools: they are optional services the app calls at run time, and only
when a key is set in `.env`. Without keys the app runs with no AI at all.

| Service | Made by | What the app uses it for | Setting |
|---|---|---|---|
| Gemini | Google | English case sheet and a short doctor's brief from Hindi or Marathi answers; up to three extra follow-up questions; reading photos of old prescriptions and lab reports; speech-to-text and read-aloud when there is no Sarvam key | `GEMINI_API_KEY` |
| Sarvam AI | Sarvam | Hindi and Marathi speech-to-text and natural Indian voices | `SARVAM_API_KEY` |

Limits the code enforces on these services:

- AI never diagnoses, never suggests a medicine and never writes the doctor's notes. Every AI
  output is shown to the doctor as a labelled draft, which the doctor accepts, edits or rejects.
- Danger signs are found by fixed rules, not by AI. AI cannot lower a patient's urgency.
- `LLM_ENABLED=false` switches AI off for the whole installation, and each hospital can switch it
  off in Settings. `PROVIDER_MODE=demo-safe` replays recorded answers with no network calls.
- The automated tests never call a live AI or speech service
  ([`services/api/tests/conftest.py`](services/api/tests/conftest.py) blanks both keys).

When keys are set, the patient's answers (and voice and photos, for the features that need them)
are sent to these services. Use synthetic data only; see [`docs/PRIVACY.md`](docs/PRIVACY.md)
and [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

## How the AI tools were used

1. The team defined the product and split the build into phases. The specification the coding
   agents worked from is [`AGENT_PLAYBOOK.md`](AGENT_PLAYBOOK.md); the plan and the result of
   each phase are in [`docs/phase-plans/`](docs/phase-plans/) and
   [`docs/phase-reports/`](docs/phase-reports/).
2. For each task, a coding agent wrote the change and its tests on a branch.
3. Every change went through a pull request. CI ran lint and type checks, the backend and web
   tests, the end-to-end tests, the synthetic evaluations, security scans and a production
   smoke test ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
4. The team tested the result in the running app and reviewed it before it was merged.

## What the team did

- **Problem research:** how history-taking and queues work in crowded government OPDs, and what
  patients, nurses and doctors there need.
- **Design:** the product, the patient and staff flows, the three languages, and the safety
  boundary (history-taking and queue only, no diagnosis).
- **Testing:** used the kiosk, the staff screens and the phone pages in the running app after each
  phase, and checked that the automated tests cover the behaviour that matters.
- **Review:** read, corrected and approved the AI-written changes before merging them.
- **Demo:** the demo script ([`docs/DEMO.md`](docs/DEMO.md)), the rehearsals and the presentation.

The team is responsible for all of the code and documentation here, whoever or whatever wrote the
first draft.
