"""One visit as a FHIR R4 bundle, for the hospital's own systems (Phase 7, task 7.10.1).

Plain dictionaries in, a FHIR ``Bundle`` (type ``collection``) out; no FHIR library is needed
at run time (the tests validate every bundle with one, ADR 017). Everything the patient said
stays marked as theirs: complaints and conditions are unconfirmed and asserted by the patient
(never diagnoses), medicines and allergies are as stated, and results come only from papers a
doctor accepted. The bundle is tagged "not ABDM-certified" until ABDM certification (7.10.3).
"""

import re
from datetime import date, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

TERMINOLOGY = "http://terminology.hl7.org/CodeSystem"
SYSTEM = "https://opd-saathi.example/fhir"
NOT_CERTIFIED = {
    "system": f"{SYSTEM}/tags",
    "code": "not-abdm-certified",
    "display": "Not ABDM-certified",
}
_ENCOUNTER_STATUS = {
    "registering": "arrived",
    "awaiting_confirmation": "arrived",
    "queued": "triaged",
    "called": "triaged",
    "skipped": "triaged",
    "in_consult": "in-progress",
    "done": "finished",
    "no_show": "cancelled",
    "cancelled": "cancelled",
}
_RELATIONSHIP = {"guardian": "Parent or guardian", "representative": "Someone helping the patient"}
_CONSENT_PURPOSE = {
    "care": "Collecting the history for this visit",
    "ai": "Online AI help for this visit",
    "sms": "Queue messages by SMS",
}
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def _id(visit_id: UUID, *parts: object) -> str:
    """A stable id for a derived resource, so exporting twice gives the same bundle ids."""
    return str(uuid5(NAMESPACE_URL, f"opd-saathi:{visit_id}:" + ":".join(map(str, parts))))


def _ref(resource: dict[str, Any]) -> dict[str, str]:
    return {"reference": f"urn:uuid:{resource['id']}"}


def _when(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _status(system: str, code: str) -> dict[str, Any]:
    return {"coding": [{"system": f"{TERMINOLOGY}/{system}", "code": code}]}


def _text(value: Any) -> str:
    """English text of a case-sheet entry (a string, a text pair or a medicine)."""
    if isinstance(value, dict):
        return str(value.get("text_en") or value.get("text_original") or value.get("name") or "")
    return str(value or "")


def build_visit_bundle(
    *,
    visit: dict[str, Any],
    case_sheet: dict[str, Any] | None,
    turns: list[dict[str, Any]],
    papers: list[dict[str, Any]],
    consents: list[dict[str, Any]],
    questions: dict[str, str] | None = None,
    now: datetime,
) -> dict[str, Any]:
    """Build the bundle for one visit.

    ``visit``: ``visit_id``, ``patient_id``, ``mrn``, ``sex``, ``year_of_birth``, ``status``,
    ``registered_at``, ``consult_ended_at``, ``department_code``, ``department_name``,
    ``language`` and ``answered_by``. ``papers``: accepted papers (``id``, ``kind``, ``mime``,
    ``created_at``, ``document_date``, ``service_date`` and the decoded ``reading``).
    ``consents``: rows with ``purpose``, ``granted``, ``granted_at``, ``withdrawn_at``,
    ``text_version``. ``questions``: English wording of each question id, when known.
    """
    visit_id = UUID(str(visit["visit_id"]))
    sheet = case_sheet or {}
    questions = questions or {}
    sex = visit.get("sex")
    patient: dict[str, Any] = {
        "resourceType": "Patient",
        "id": str(visit["patient_id"]),
        "gender": sex if sex in {"female", "male", "other"} else "unknown",
    }
    if visit.get("mrn"):
        patient["identifier"] = [
            {
                "system": f"{SYSTEM}/hospital/{visit['hospital_id']}/opd-number",
                "value": str(visit["mrn"]),
            }
        ]
    if visit.get("year_of_birth"):
        patient["birthDate"] = f"{int(visit['year_of_birth']):04d}"
    encounter: dict[str, Any] = {
        "resourceType": "Encounter",
        "id": str(visit_id),
        "status": _ENCOUNTER_STATUS.get(str(visit.get("status")), "unknown"),
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        },
        "subject": _ref(patient),
        "period": {
            key: value
            for key, value in (
                ("start", _when(visit.get("registered_at"))),
                ("end", _when(visit.get("consult_ended_at"))),
            )
            if value
        },
    }
    if visit.get("department_name"):
        encounter["serviceType"] = {
            "coding": [
                {
                    "system": f"{SYSTEM}/department",
                    "code": str(visit.get("department_code") or visit["department_name"]),
                    "display": str(visit["department_name"]),
                }
            ]
        }
    complaint = _text(sheet.get("chief_complaint"))
    if complaint:
        encounter["reasonCode"] = [{"text": complaint}]
    resources: list[dict[str, Any]] = [patient, encounter]

    # Who answered, when it was not the patient (task 7.11.7).
    source = _ref(patient)
    answered_by = str(visit.get("answered_by") or "patient")
    if answered_by in _RELATIONSHIP:
        related = {
            "resourceType": "RelatedPerson",
            "id": _id(visit_id, "related-person"),
            "patient": _ref(patient),
            "relationship": [{"text": _RELATIONSHIP[answered_by]}],
        }
        resources.append(related)
        source = _ref(related)

    def reported(kind: str, index: int, text: str, category: str) -> dict[str, Any]:
        return {
            "resourceType": "Condition",
            "id": _id(visit_id, kind, index),
            "clinicalStatus": _status("condition-clinical", "active"),
            "verificationStatus": _status("condition-ver-status", "unconfirmed"),
            "category": [{"text": category}],
            "code": {"text": text},
            "subject": _ref(patient),
            "encounter": _ref(encounter),
            "asserter": source,
            "note": [{"text": "Reported by the patient at check-in. Not a diagnosis."}],
        }

    if complaint:
        resources.append(reported("complaint", 0, complaint, "Chief complaint"))
    conditions = (sheet.get("past_history") or {}).get("conditions") or []
    for index, condition in enumerate(conditions):
        if _text(condition):
            resources.append(reported("condition", index, _text(condition), "Past history"))

    for index, allergy in enumerate(sheet.get("allergies") or []):
        if not _text(allergy):
            continue
        resources.append(
            {
                "resourceType": "AllergyIntolerance",
                "id": _id(visit_id, "allergy", index),
                "clinicalStatus": _status("allergyintolerance-clinical", "active"),
                "verificationStatus": _status("allergyintolerance-verification", "unconfirmed"),
                "code": {"text": _text(allergy)},
                "patient": _ref(patient),
                "encounter": _ref(encounter),
                "asserter": source,
            }
        )

    for index, medicine in enumerate(sheet.get("medications") or []):
        name = _text(medicine)
        if not name:
            continue
        statement: dict[str, Any] = {
            "resourceType": "MedicationStatement",
            "id": _id(visit_id, "medication", index),
            "status": "active",
            "medicationCodeableConcept": {"text": name},
            "subject": _ref(patient),
            "context": _ref(encounter),
            "informationSource": source,
            "note": [{"text": "As stated by the patient."}],
        }
        if isinstance(medicine, dict) and medicine.get("dose"):
            statement["dosage"] = [{"text": str(medicine["dose"])}]
        resources.append(statement)

    for paper in papers:
        reading = paper.get("reading") or {}
        document = {
            "resourceType": "DocumentReference",
            "id": str(paper["id"]),
            "status": "current",
            "type": {"text": str(paper.get("kind") or "other").replace("_", " ")},
            "subject": _ref(patient),
            "date": _when(paper.get("created_at")),
            "content": [
                {
                    "attachment": {
                        "contentType": str(paper.get("mime") or "image/jpeg"),
                        "title": "Photo of a paper the patient brought",
                    }
                }
            ],
            "context": {"encounter": [_ref(encounter)]},
        }
        resources.append(document)
        effective = paper.get("document_date") or paper.get("service_date")
        for index, result in enumerate(reading.get("lab_results") or []):
            if not isinstance(result, dict) or not result.get("test"):
                continue
            observation: dict[str, Any] = {
                "resourceType": "Observation",
                "id": _id(visit_id, "result", paper["id"], index),
                "status": "final",
                "category": [_status("observation-category", "laboratory")],
                "code": {"text": str(result["test"])},
                "subject": _ref(patient),
                "encounter": _ref(encounter),
                "effectiveDateTime": _when(effective),
                "derivedFrom": [_ref(document)],
                "note": [
                    {"text": "Read from a paper the patient brought; accepted by a doctor."}
                ],
            }
            value, unit = str(result.get("value", "")).strip(), str(result.get("unit", "")).strip()
            if _NUMBER.match(value) and unit:
                observation["valueQuantity"] = {"value": float(value), "unit": unit}
            elif value:
                observation["valueString"] = f"{value} {unit}".strip()
            if result.get("reference_range"):
                observation["referenceRange"] = [{"text": str(result["reference_range"])}]
            flag = {"high": "H", "low": "L"}.get(str(result.get("printed_flag")))
            if flag:
                observation["interpretation"] = [
                    {
                        "coding": [
                            {
                                "system": f"{TERMINOLOGY}/v3-ObservationInterpretation",
                                "code": flag,
                            }
                        ],
                        "text": "As marked on the report",
                    }
                ]
            resources.append(observation)

    answers: dict[str, dict[str, Any]] = {}
    for turn in turns:  # in order; a corrected answer replaces the earlier one
        said = turn.get("text_original") or turn.get("text_english")
        if not said:
            continue
        item: dict[str, Any] = {
            "linkId": str(turn["question_id"]),
            "answer": [{"valueString": str(said)}],
        }
        wording = questions.get(str(turn["question_id"]))
        if wording:
            item["text"] = wording
        answers[str(turn["question_id"])] = item
    if answers:
        resources.append(
            {
                "resourceType": "QuestionnaireResponse",
                "id": _id(visit_id, "interview"),
                "status": "completed",
                "subject": _ref(patient),
                "encounter": _ref(encounter),
                "authored": _when(visit.get("registered_at")),
                "source": source,
                "item": list(answers.values()),
            }
        )

    for row in consents:
        purpose = str(row.get("purpose") or "care")
        if not row.get("granted"):
            status = "rejected"
        elif row.get("withdrawn_at"):
            status = "inactive"
        else:
            status = "active"
        consent: dict[str, Any] = {
            "resourceType": "Consent",
            "id": _id(visit_id, "consent", purpose),
            "status": status,
            "scope": _status("consentscope", "patient-privacy"),
            "category": [{"coding": [{"system": "http://loinc.org", "code": "59284-0"}]}],
            "patient": _ref(patient),
            "performer": [source],
            "policyRule": {
                "text": f"{_CONSENT_PURPOSE.get(purpose, purpose)} "
                f"(notice {row.get('text_version', '')})".strip()
            },
        }
        if row.get("granted_at"):
            consent["dateTime"] = _when(row["granted_at"])
        resources.append(consent)

    return {
        "resourceType": "Bundle",
        "id": _id(visit_id, "bundle"),
        "meta": {"tag": [NOT_CERTIFIED]},
        "identifier": {"system": f"{SYSTEM}/visit", "value": str(visit_id)},
        "type": "collection",
        "timestamp": now.isoformat(),
        "entry": [
            {"fullUrl": f"urn:uuid:{resource['id']}", "resource": resource}
            for resource in resources
        ],
    }
