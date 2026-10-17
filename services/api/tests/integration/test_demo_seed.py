"""The demo's returning patient: her OPD card, earlier visit and old paper (synthetic data)."""

import pytest

from app.cli import _clear_demo_returning, _seed_demo_returning
from app.demo_papers import prescription_png
from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)


async def test_the_returning_patient_links_by_card_and_brings_her_earlier_paper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with synthetic_hospital() as hospital:
        monkeypatch.setattr("app.cli.get_settings", lambda: hospital.settings)
        card = await _seed_demo_returning(hospital.hospital_id)
        # Seeding again changes nothing and prints the same card.
        assert await _seed_demo_returning(hospital.hospital_id) == card

        with api_client(hospital.settings) as client:
            today = start_kiosk_session(client, activate_kiosk(client, hospital))
            today.agree()
            assert today.identity(age=52, sex="female").status_code == 200
            linked = today.post("/opd-card", {"number": card["opd_number"], "pin": card["pin"]})
            assert linked.status_code == 200, linked.text
            earlier = linked.json()["previous_visit"]
            assert earlier["id"] == card["visit_id"]
            assert today.post("/follow-up", {"previous_visit_id": earlier["id"]}).status_code == 200
            today.danger_signs()
            today.answer_all({"chief_concern": "tiredness"})
            assert today.confirm().status_code == 200

            doctor = hospital.staff("doctor")
            history = client.get(f"/visits/{today.visit_id}/history", headers=doctor).json()
            (previous,) = history["previous_visits"]
            assert previous["chief_complaint"] == "Tiredness and a lot of thirst"
            assert history["follow_up_of"] == card["visit_id"]
            assert [item["text"] for item in history["known_allergies"]] == ["Penicillin"]
            assert {item["text"] for item in history["known_medications"]} == {
                "Metformin 500 mg twice a day",
                "Amlodipine 5 mg at night",
            }
            papers = client.get(f"/visits/{today.visit_id}/documents", headers=doctor).json()
            (paper,) = papers["earlier"]
            assert paper["kind"] == "prescription" and paper["reading_status"] == "skipped"
            assert paper["visit_id"] == card["visit_id"]
            photo = client.get(
                f"/visits/{today.visit_id}/documents/{paper['id']}/content", headers=doctor
            )
            assert photo.status_code == 200 and photo.content == prescription_png()

        # Before the next run-through: today's visit goes, her card and earlier visit stay.
        assert await _seed_demo_returning(hospital.hospital_id, reset=True) == card
        visits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT id FROM visits WHERE hospital_id = :h AND patient_id = "
            "(SELECT patient_id FROM visits WHERE hospital_id = :h AND id = :v)",
            h=hospital.hospital_id,
            v=card["visit_id"],
        )
        assert [str(row["id"]) for row in visits] == [card["visit_id"]]

        assert await _clear_demo_returning(hospital.hospital_id) == 1
        left = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT (SELECT count(*) FROM visits WHERE hospital_id = :h AND id = :v) AS visits, "
            "(SELECT count(*) FROM documents WHERE hospital_id = :h AND visit_id = :v) AS papers",
            h=hospital.hospital_id,
            v=card["visit_id"],
        )
        assert dict(left[0]) == {"visits": 0, "papers": 0}
