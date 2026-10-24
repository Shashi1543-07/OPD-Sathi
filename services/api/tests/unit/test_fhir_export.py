"""The FHIR export of one visit validates against FHIR R4 (R4B models) and keeps the rules."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.domain.fhir_export import NOT_CERTIFIED, build_visit_bundle

R4B = pytest.importorskip("fhir.resources.R4B.bundle")

VISIT_ID, PATIENT_ID, HOSPITAL_ID, PAPER_ID = uuid4(), uuid4(), uuid4(), uuid4()
NOW = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)


def _visit(**overrides: object) -> dict[str, object]:
    return {
        "visit_id": VISIT_ID,
        "patient_id": PATIENT_ID,
        "hospital_id": HOSPITAL_ID,
        "mrn": "26-000042",
        "sex": "female",
        "year_of_birth": 1986,
        "status": "done",
        "registered_at": datetime(2026, 10, 24, 4, 0, tzinfo=UTC),
        "consult_ended_at": datetime(2026, 10, 24, 5, 0, tzinfo=UTC),
        "department_code": "GENMED",
        "department_name": "General Medicine",
        "language": "hi",
        "answered_by": "patient",
        **overrides,
    }


SHEET = {
    "chief_complaint": {"text_original": "बुखार", "text_en": "Fever"},
    "past_history": {"conditions": ["Diabetes", ""]},
    "allergies": [{"text_original": "पेनिसिलिन", "text_en": "Penicillin"}],
    "medications": [{"name": "Metformin", "dose": "500 mg twice a day"}, {"name": ""}],
}
PAPER = {
    "id": PAPER_ID,
    "kind": "lab_report",
    "mime": "image/jpeg",
    "created_at": datetime(2026, 10, 24, 4, 10, tzinfo=UTC),
    "document_date": date(2026, 9, 30),
    "service_date": date(2026, 10, 24),
    "reading": {
        "lab_results": [
            {
                "test": "Haemoglobin",
                "value": "9.8",
                "unit": "g/dL",
                "reference_range": "12.0 - 15.0",
                "printed_flag": "low",
            },
            {"test": "Urine culture", "value": "No growth", "unit": "", "reference_range": ""},
            {"test": "", "value": "ignored"},
        ]
    },
}
TURNS = [
    {"question_id": "chief_concern", "text_original": "बुखार", "text_english": "Fever"},
    {"question_id": "duration", "text_original": "2 days", "text_english": "2 days"},
    {"question_id": "duration", "text_original": "3 days", "text_english": "3 days"},
    {"question_id": "severity", "text_original": None, "text_english": None},
]
CONSENTS = [
    {"purpose": "care", "granted": True, "granted_at": NOW, "text_version": "care-1.0"},
    {"purpose": "ai", "granted": True, "granted_at": NOW, "withdrawn_at": NOW,
     "text_version": "ai-1.0"},
    {"purpose": "sms", "granted": False, "granted_at": None, "text_version": "sms-1.0"},
]


def _bundle(**visit: object) -> dict[str, object]:
    return build_visit_bundle(
        visit=_visit(**visit),
        case_sheet=SHEET,
        turns=TURNS,
        papers=[PAPER],
        consents=CONSENTS,
        questions={"duration": "Since when?"},
        now=NOW,
    )


def _of(bundle: dict[str, object], kind: str) -> list[dict[str, object]]:
    return [
        entry["resource"]
        for entry in bundle["entry"]  # type: ignore[union-attr]
        if entry["resource"]["resourceType"] == kind
    ]


def test_the_bundle_validates_as_fhir_r4_and_is_tagged_not_certified() -> None:
    bundle = _bundle()
    parsed = R4B.Bundle.model_validate(bundle)
    assert parsed.type == "collection"
    assert bundle["meta"] == {"tag": [NOT_CERTIFIED]}
    kinds = sorted(entry["resource"]["resourceType"] for entry in bundle["entry"])
    assert kinds == sorted(
        [
            "Patient",
            "Encounter",
            "Condition",
            "Condition",
            "AllergyIntolerance",
            "MedicationStatement",
            "DocumentReference",
            "Observation",
            "Observation",
            "QuestionnaireResponse",
            "Consent",
            "Consent",
            "Consent",
        ]
    )
    # Every reference points at a resource in the bundle.
    urls = {entry["fullUrl"] for entry in bundle["entry"]}
    assert all(url.startswith("urn:uuid:") for url in urls)
    assert f"urn:uuid:{VISIT_ID}" in urls and f"urn:uuid:{PATIENT_ID}" in urls
    # Exporting again gives the same ids.
    assert [entry["fullUrl"] for entry in _bundle()["entry"]] == [
        entry["fullUrl"] for entry in bundle["entry"]
    ]


def test_what_the_patient_said_stays_unconfirmed_and_theirs() -> None:
    bundle = _bundle()
    (patient,) = _of(bundle, "Patient")
    assert patient["identifier"][0]["value"] == "26-000042"
    assert patient["birthDate"] == "1986" and "name" not in patient
    (encounter,) = _of(bundle, "Encounter")
    assert encounter["status"] == "finished" and encounter["reasonCode"] == [{"text": "Fever"}]
    for condition in _of(bundle, "Condition"):
        assert condition["verificationStatus"]["coding"][0]["code"] == "unconfirmed"
        assert condition["asserter"] == {"reference": f"urn:uuid:{PATIENT_ID}"}
        assert "Not a diagnosis" in condition["note"][0]["text"]
    (allergy,) = _of(bundle, "AllergyIntolerance")
    assert allergy["code"] == {"text": "Penicillin"}
    (medicine,) = _of(bundle, "MedicationStatement")
    assert medicine["dosage"] == [{"text": "500 mg twice a day"}]


def test_results_keep_their_printed_range_and_the_report_s_own_mark() -> None:
    haemoglobin, culture = _of(_bundle(), "Observation")
    assert haemoglobin["valueQuantity"] == {"value": 9.8, "unit": "g/dL"}
    assert haemoglobin["referenceRange"] == [{"text": "12.0 - 15.0"}]
    assert haemoglobin["interpretation"][0]["coding"][0]["code"] == "L"
    assert haemoglobin["effectiveDateTime"] == "2026-09-30"
    assert haemoglobin["derivedFrom"] == [{"reference": f"urn:uuid:{PAPER_ID}"}]
    assert culture["valueString"] == "No growth" and "interpretation" not in culture


def test_the_interview_keeps_the_last_answer_and_the_consents_their_state() -> None:
    bundle = _bundle()
    (interview,) = _of(bundle, "QuestionnaireResponse")
    items = {item["linkId"]: item for item in interview["item"]}
    assert set(items) == {"chief_concern", "duration"}
    assert items["duration"]["answer"] == [{"valueString": "3 days"}]
    assert items["duration"]["text"] == "Since when?"
    states = {consent["id"]: consent["status"] for consent in _of(bundle, "Consent")}
    assert sorted(states.values()) == ["active", "inactive", "rejected"]


def test_a_parent_answering_is_the_source_and_the_consenting_party() -> None:
    bundle = _bundle(answered_by="guardian", status="in_consult", mrn=None, year_of_birth=None)
    R4B.Bundle.model_validate(bundle)
    (related,) = _of(bundle, "RelatedPerson")
    assert related["relationship"] == [{"text": "Parent or guardian"}]
    source = {"reference": f"urn:uuid:{related['id']}"}
    (interview,) = _of(bundle, "QuestionnaireResponse")
    assert interview["source"] == source
    assert all(consent["performer"] == [source] for consent in _of(bundle, "Consent"))
    (encounter,) = _of(bundle, "Encounter")
    assert encounter["status"] == "in-progress"
    (patient,) = _of(bundle, "Patient")
    assert "identifier" not in patient and "birthDate" not in patient


def test_a_visit_with_little_recorded_still_validates() -> None:
    bundle = build_visit_bundle(
        visit=_visit(status="registering", consult_ended_at=None, department_name=None,
                     sex="unknown"),
        case_sheet=None,
        turns=[],
        papers=[],
        consents=[],
        now=NOW,
    )
    R4B.Bundle.model_validate(bundle)
    assert [entry["resource"]["resourceType"] for entry in bundle["entry"]] == [
        "Patient",
        "Encounter",
    ]


# FHIR R4 value sets with a required binding, for the codes the export sets. fhir.resources
# checks structure and cardinality but not these bindings, so they are checked here (ADR 017).
_REQUIRED = {
    ("Encounter", "status"): {
        "planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled",
        "entered-in-error", "unknown",
    },
    ("Observation", "status"): {
        "registered", "preliminary", "final", "amended", "corrected", "cancelled",
        "entered-in-error", "unknown",
    },
    ("MedicationStatement", "status"): {
        "active", "completed", "entered-in-error", "intended", "stopped", "on-hold", "unknown",
        "not-taken",
    },
    ("DocumentReference", "status"): {"current", "superseded", "entered-in-error"},
    ("QuestionnaireResponse", "status"): {
        "in-progress", "completed", "amended", "entered-in-error", "stopped",
    },
    ("Consent", "status"): {
        "draft", "proposed", "active", "rejected", "inactive", "entered-in-error",
    },
    ("Bundle", "type"): {
        "document", "message", "transaction", "transaction-response", "batch",
        "batch-response", "history", "searchset", "collection",
    },
}
_CODED = {
    ("Condition", "clinicalStatus"): {
        "active", "recurrence", "relapse", "inactive", "remission", "resolved",
    },
    ("Condition", "verificationStatus"): {
        "unconfirmed", "provisional", "differential", "confirmed", "refuted", "entered-in-error",
    },
    ("AllergyIntolerance", "clinicalStatus"): {"active", "inactive", "resolved"},
    ("AllergyIntolerance", "verificationStatus"): {
        "unconfirmed", "confirmed", "refuted", "entered-in-error",
    },
}


@pytest.mark.parametrize("answered_by", ["patient", "guardian"])
@pytest.mark.parametrize("status", ["registering", "queued", "in_consult", "done", "no_show"])
def test_every_required_code_is_in_its_fhir_value_set(status: str, answered_by: str) -> None:
    bundle = _bundle(status=status, answered_by=answered_by)
    assert bundle["type"] in _REQUIRED[("Bundle", "type")]
    for entry in bundle["entry"]:
        resource = entry["resource"]
        for (kind, field), allowed in _REQUIRED.items():
            if resource["resourceType"] == kind and field in resource:
                assert resource[field] in allowed, (kind, field, resource[field])
        for (kind, field), allowed in _CODED.items():
            if resource["resourceType"] == kind:
                assert resource[field]["coding"][0]["code"] in allowed, (kind, field)
