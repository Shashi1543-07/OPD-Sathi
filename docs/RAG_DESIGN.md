# A records assistant for doctors: design only

A design, not a plan to build. It sets the rules any future question-answering over a
patient's records must follow, so that if one is built it starts from them. Nothing here is
implemented, and nothing should be without an ADR, the owner's approval (ADR 001) and clinical
sign-off. Written 24 Oct 2026 (Phase 7, task 7.11.11; out of scope for v1.0).

## What it would do

A doctor looking after today's visit asks a question about this patient's earlier records in
plain language ("When was her last haemoglobin, and what was it?", "Has he reported a penicillin
allergy before?") and gets a short answer that cites the records it comes from, or a clear
"not found in the records".

## What it would never do

- Diagnose, suggest a medicine, a dose or a test, or comment on interactions (ADR 015). Such
  questions get a fixed refusal that points to the doctor's own judgement and references.
- Answer from general medical knowledge. Only this patient's records, in this hospital.
- Answer patients. It is a staff tool, behind the same sign-in and department rules as the
  case sheet.
- Treat what it retrieved as instructions (see "Retrieved text is data").

## Rules

### 1. Scope before search

The set of records searched is fixed **before** any retrieval, by the same rules that already
decide what a doctor may open:

- the hospital: the request runs under that hospital's row-level security scope, so the index
  query itself cannot reach another hospital's rows;
- the patient: only records of the patient linked to the visit being treated (by OPD card or a
  nurse's check), never a search across patients;
- care: only while the patient is under care today, as for earlier visits and papers
  (`visit_in_care`), and only for staff of the treating department or administrators;
- what may be shown: no AI draft from a visit where the patient stopped AI help, and no paper
  reading a doctor has not accepted (as in "Results over time").

A vector index, if one is used, holds one entry per record with `hospital_id`, `patient_id` and
`visit_id`, and every query filters on them inside the database; similarity search never runs
first and filters afterwards.

### 2. Every factual sentence cites its records

The answer is a list of sentences, each with the ids of the records it rests on (case sheet
field, doctor's note version, accepted paper reading). As with the AI draft today, a sentence
without a valid citation is dropped before the doctor sees it. The interface shows each
citation next to its sentence and opens the record in one tap.

### 3. Refuse when the records do not say

If nothing retrieved supports an answer, the reply is "Not found in this patient's records",
never a guess. A partial answer says which part was not found.

### 4. Retrieved text is data

Records contain the patient's own words and text read from photos. They are passed to the model
as quoted data, never mixed with its instructions, and the output is checked against the same
guards as the AI draft (no instructions followed from records, no medicines suggested, every
sentence cited). The red-flag rules run over the question and the answer like over interview
text: an urgent sign in either is shown to the doctor as an alert, not left in prose.

### 5. Privacy

- The same consent as the AI draft: no patient data goes to an AI service for a visit whose
  patient said no or stopped AI help. Embeddings, if computed by an external service, count as
  sending data and follow the same rule; computing them on the server avoids that.
- Questions and answers are not kept beyond the visit; each use is audited (who asked, which
  visit, which records were cited), never the text.
- The index is derived data: removed with the records by the retention job, rebuilt from
  PostgreSQL, never backed up on its own.

## How it would be evaluated

Before any pilot, a synthetic evaluation set in the three languages:

- questions with a known answer in the records (correct value and citation);
- questions whose answer is absent (must refuse);
- questions about another patient, another hospital or a closed visit (must not answer);
- requests for diagnosis, medicines or doses (must refuse);
- records containing injected instructions (must be ignored);
- red-flag wording in questions and records (must alert).

Pass marks are set with the clinical lead before building. Every answer in the set must cite
correctly; any answer from outside scope fails the evaluation outright.

## What it would need

An ADR (and the owner's approval) for any new component (an index or embedding model), a
clinical safety case and hazard-log entries, counsel's view on the AI processing, the
evaluation set above, and staff training on what it can and cannot be asked.
