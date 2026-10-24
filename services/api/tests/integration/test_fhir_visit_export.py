"""A visit exported as FHIR R4: valid, only accepted readings, for the right staff (7.10.1)."""

from datetime import date, timedelta

import pytest

from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)
from tests.integration.test_investigations import _paper, _read, _reading

R4B = pytest.importorskip("fhir.resources.R4B.bundle")


async def test_a_visit_exports_as_a_valid_fhir_bundle_with_accepted_results_only() -> None:
    printed = date.today() - timedelta(days=20)
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            session.agree("guardian")
            said = session.identity(age=8, sex="female", answered_by="guardian")
            assert said.status_code == 200, said.text
            accepted, unreviewed = _paper(session), _paper(session)
            session.danger_signs()
            session.answer_all({"chief_concern": "fever", "allergies": "penicillin"})
            assert session.confirm().status_code == 200

        await _read(hospital, accepted, _reading(printed, "10.4", "L"), printed=printed,
                    decision="accepted")
        await _read(hospital, unreviewed, _reading(printed, "1.0"), printed=printed,
                    decision="unreviewed")

        with api_client(hospital.settings) as client:
            response = client.get(
                f"/visits/{session.visit_id}/fhir", headers=hospital.staff("doctor")
            )
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("application/fhir+json")
            bundle = response.json()
            R4B.Bundle.model_validate(bundle)
            kinds = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
            assert {"Patient", "Encounter", "RelatedPerson", "QuestionnaireResponse",
                    "Consent", "AllergyIntolerance"} <= set(kinds)
            observations = [
                entry["resource"] for entry in bundle["entry"]
                if entry["resource"]["resourceType"] == "Observation"
            ]
            assert [obs["valueQuantity"]["value"] for obs in observations] == [10.4]
            assert kinds.count("DocumentReference") == 1
            assert bundle["meta"]["tag"][0]["code"] == "not-abdm-certified"

            nurse = client.get(f"/visits/{session.visit_id}/fhir", headers=hospital.staff("nurse"))
            assert nurse.status_code == 403
            other = client.get(
                f"/visits/{session.visit_id}/fhir", headers=hospital.staff("ortho_doctor")
            )
            assert other.status_code == 404

        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h AND action = 'fhir.exported'",
            h=hospital.hospital_id,
        )
        assert audits[0]["meta"] == {"resources": len(bundle["entry"])}
