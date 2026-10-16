# Problem statement

**Status: verified from secondary copies on 16 October 2026; official wording still to be confirmed.**
The official listing (<https://sih.gov.in/sih2026PS>) loads problem statements in pages and SIH26047 is
not in the part that can be fetched automatically. Two independent copies of the official entry agree
on every field below except the theme. Before submission, open the official portal in a browser, search
for 26047, and record any differences here.

- **ID and title:** SIH26047 — Patient Case-Taking Software
- **Organisation:** Ministry of Ayush
- **Department:** All India Institute of Ayurveda (AIIA)
- **Category:** Software
- **Theme:** MedTech / BioTech / HealthTech (one copy lists "Smart Automation")

## What the statement asks for

Outpatient consultations in Indian public hospitals last only minutes, so histories are often
incomplete, and AYUSH practice needs a more extensive history (Prakriti, Vikriti, Agni). Patients bring
fragmented paper prescriptions, reports and discharge summaries. ABDM provides national infrastructure,
but no patient-facing tool solves the "first mile": capturing history and digitising documents before
the consultation.

The expected solution has four modules:

- **A. Conversational history engine:** adaptive voice-and-touch interview in the patient's language,
  SOCRATES-style follow-up questions, an AYUSH extended mode (Dashavidha Pariksha), and red-flag
  detection that raises priority triage alerts.
- **B. Document digitisation:** multilingual OCR for printed and handwritten documents; extraction of
  diagnoses, medicines, lab values and procedures; a chronological timeline; flags for abnormal values
  and potential drug interactions.
- **C. Summary generator:** a standard-format summary (chief complaint, HPI, past history, drugs and
  allergies, family, personal, review of systems, prior investigations) that the physician can edit,
  accept or reject. It is a draft, never an autonomous diagnosis. Patient-facing audio is in the local
  language; physician-facing output is in English or Hindi.
- **D. Consent, privacy and ABDM integration:** ABHA-based authentication, granular and revocable consent
  with audio explanation, secure processing, session data cleared after submission, and FHIR-based
  transfer to the hospital system and the ABHA personal health record, in line with the DPDP Act 2023.

## How OPD Saathi maps to it

OPD Saathi collects a structured patient history before the consultation and helps staff manage the
queue. It does not diagnose or recommend treatment. The requirement-by-requirement mapping, current
gaps and planned work are in [IITB_READINESS_PLAN.md](IITB_READINESS_PLAN.md#21-requirements-matrix).

## Sources

- Official listing: <https://sih.gov.in/sih2026PS> (SIH26047 not reachable by automated fetch)
- Copy of the full entry: <https://sih-explorer.amanuniyal47.workers.dev/problem/SIH26047>
- Repository quoting the official metadata: <https://github.com/xarjunpatil/SIH26047-Patient-Case-Taking-Software>
