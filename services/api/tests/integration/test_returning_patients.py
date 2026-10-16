"""Returning patients: OPD card, shared phones, history for the treating clinician (synthetic)."""

from typing import Any

from fastapi.testclient import TestClient

from tests.integration.synthetic import (
    PatientSession,
    activate_kiosk,
    api_client,
    complete_intake,
    execute,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)


def _first_visit(
    client: TestClient,
    kiosk: dict[str, str],
    *,
    age: int = 40,
    sex: str = "female",
    phone: str | None = None,
    answers: dict[str, str | int] | None = None,
) -> tuple[PatientSession, dict[str, Any]]:
    session = start_kiosk_session(client, kiosk)
    complete_intake(session, complaint="fever", age=age, sex=sex, phone=phone, answers=answers)
    receipt = session.confirm()
    assert receipt.status_code == 200, receipt.text
    return session, dict(receipt.json())


def _start_returning(
    client: TestClient, kiosk: dict[str, str], *, age: int = 40, sex: str = "female"
) -> PatientSession:
    session = start_kiosk_session(client, kiosk)
    session.agree()
    assert session.identity(age=age, sex=sex).status_code == 200
    return session


async def test_a_shared_phone_number_no_longer_blocks_a_second_patient() -> None:
    """D3: the unique phone index refused returning patients and families sharing a phone."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            _first_visit(client, kiosk, phone="9876500001")
            second = start_kiosk_session(client, kiosk)
            second.agree()
            identity = second.identity(age=12, sex="male", phone="9876500001")
            assert identity.status_code == 200, identity.text


async def test_first_visit_gets_an_opd_card_and_the_next_visit_links_with_it() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            first, receipt = _first_visit(
                client, kiosk, phone="9876500002", answers={"allergies": "penicillin"}
            )
            card = receipt["opd_card"]
            assert card["number"].endswith("-000001") and len(card["pin"]) == 6

            returning = _start_returning(client, kiosk, age=41)
            linked = returning.post("/opd-card", {"number": card["number"], "pin": card["pin"]})
            assert linked.status_code == 200, linked.text
            body = linked.json()
            assert body["visit_count"] == 1 and body["opd_number"] == card["number"]
            # The kiosk learns only the date of the earlier visit, never what it was about.
            assert set(body["previous_visit"]) == {"id", "date"}
            follow_up = returning.post(
                "/follow-up", {"previous_visit_id": body["previous_visit"]["id"]}
            )
            assert follow_up.status_code == 200, follow_up.text

            returning.danger_signs()
            returning.answer_all({"chief_concern": "fever again"})
            second_receipt = returning.confirm()
            assert second_receipt.status_code == 200, second_receipt.text
            assert second_receipt.json()["opd_card"] == card

            history = client.get(
                f"/visits/{returning.visit_id}/history", headers=hospital.staff("doctor")
            )
            assert history.status_code == 200, history.text
            data = history.json()
            assert data["opd_number"] == card["number"]
            assert data["linked_by"] == "card"
            assert data["follow_up_of"] == first.visit_id
            assert [visit["visit_id"] for visit in data["previous_visits"]] == [first.visit_id]
            assert data["previous_visits"][0]["chief_complaint"]
            assert [item["text"].lower() for item in data["known_allergies"]] == ["penicillin"]

        rows = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT id, patient_id, patient_link FROM visits WHERE hospital_id = :h "
            "AND token_number IS NOT NULL ORDER BY registered_at",
            h=hospital.hospital_id,
        )
        assert len({row["patient_id"] for row in rows}) == 1
        assert [row["patient_link"] for row in rows] == [None, "card"]
        # The returning visit's own placeholder record keeps nothing personal.
        empty = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT count(*) AS n FROM patients WHERE hospital_id = :h AND mrn IS NULL "
            "AND (phone_hmac IS NOT NULL OR year_of_birth IS NOT NULL)",
            h=hospital.hospital_id,
        )
        assert empty[0]["n"] == 0


async def test_card_link_refuses_wrong_pins_unknown_numbers_and_details_that_do_not_fit() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            _, receipt = _first_visit(client, kiosk, age=40, sex="female")
            card = receipt["opd_card"]
            wrong_pin = "000000" if card["pin"] != "000000" else "111111"

            session = _start_returning(client, kiosk, age=40)
            refused = session.post("/opd-card", {"number": card["number"], "pin": wrong_pin})
            assert refused.status_code == 422 and refused.json()["code"] == "CARD_NOT_RECOGNISED"
            unknown = session.post("/opd-card", {"number": "26-999999", "pin": card["pin"]})
            assert unknown.status_code == 422 and unknown.json()["code"] == "CARD_NOT_RECOGNISED"

            older = _start_returning(client, kiosk, age=70)
            mismatch = older.post("/opd-card", card)
            assert mismatch.status_code == 409
            assert mismatch.json()["code"] == "CARD_IDENTITY_MISMATCH"
            other_sex = _start_returning(client, kiosk, age=40, sex="male")
            assert other_sex.post("/opd-card", card).json()["code"] == "CARD_IDENTITY_MISMATCH"

            guesser = _start_returning(client, kiosk, age=40)
            codes = [
                guesser.post("/opd-card", {"number": card["number"], "pin": wrong_pin}).status_code
                for _ in range(6)
            ]
            assert codes[:5] == [422] * 5 and codes[5] == 429

        refusals = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT count(*) AS n FROM audit_log WHERE hospital_id = :h "
            "AND action = 'intake.opd_card.refused'",
            h=hospital.hospital_id,
        )
        assert refusals[0]["n"] >= 9


async def test_history_stays_with_the_treating_department_and_inside_the_hospital() -> None:
    async with synthetic_hospital() as hospital, synthetic_hospital() as other:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            _, receipt = _first_visit(client, kiosk)
            returning = _start_returning(client, kiosk)
            assert returning.post("/opd-card", receipt["opd_card"]).status_code == 200

            path = f"/visits/{returning.visit_id}/history"
            assert client.get(path, headers=hospital.staff("ortho_doctor")).status_code == 404
            assert client.get(path, headers=other.staff("admin")).status_code == 404
            assert client.get(path, headers=hospital.staff("nurse")).status_code == 200

            # An OPD card from one hospital means nothing at another.
            elsewhere = _start_returning(client, activate_kiosk(client, other))
            assert elsewhere.post("/opd-card", receipt["opd_card"]).status_code == 422

        # Once the visit is closed and in the past, its doctor no longer sees the history.
        await execute(
            hospital.settings,
            hospital.hospital_id,
            "UPDATE visits SET status = 'done', service_date = service_date - 7 "
            "WHERE hospital_id = :h AND id = :v",
            h=hospital.hospital_id,
            v=returning.visit_id,
        )
        with api_client(hospital.settings) as client:
            closed = client.get(path, headers=hospital.staff("doctor"))
            assert closed.status_code == 403 and closed.json()["code"] == "HISTORY_CLOSED"
            assert client.get(path, headers=hospital.staff("admin")).status_code == 200


async def test_a_nurse_finds_and_links_a_patient_who_forgot_their_card() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            _, mother = _first_visit(client, kiosk, age=38, sex="female", phone="9876500003")
            _, child = _first_visit(client, kiosk, age=9, sex="male", phone="9876500003")

            by_phone = client.get(
                "/patients/lookup", params={"phone": "9876500003"}, headers=hospital.staff("nurse")
            )
            assert by_phone.status_code == 200, by_phone.text
            numbers = {item["opd_number"] for item in by_phone.json()["items"]}
            assert numbers == {mother["opd_card"]["number"], child["opd_card"]["number"]}
            item = by_phone.json()["items"][0]
            assert set(item) == {"opd_number", "age_years", "sex", "visit_count", "last_visit"}

            today = _start_returning(client, kiosk, age=9, sex="male")
            not_allowed = client.post(
                f"/queue/{today.visit_id}/link-patient",
                json={"opd_number": child["opd_card"]["number"]},
                headers=hospital.staff("doctor"),
            )
            assert not_allowed.status_code == 403
            linked = client.post(
                f"/queue/{today.visit_id}/link-patient",
                json={"opd_number": child["opd_card"]["number"]},
                headers=hospital.staff("nurse"),
            )
            assert linked.status_code == 200, linked.text
            history = client.get(
                f"/visits/{today.visit_id}/history", headers=hospital.staff("doctor")
            )
            assert history.json()["linked_by"] == "staff"
            assert len(history.json()["previous_visits"]) == 1


async def test_a_patient_who_forgot_the_card_and_registered_as_new_is_joined_later() -> None:
    """The number issued for that one visit is retired; the earlier record keeps its own."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            first, original = _first_visit(client, kiosk, age=52, sex="male")
            again, duplicate = _first_visit(client, kiosk, age=52, sex="male")
            assert duplicate["opd_card"]["number"] != original["opd_card"]["number"]

            linked = client.post(
                f"/queue/{again.visit_id}/link-patient",
                json={"opd_number": original["opd_card"]["number"]},
                headers=hospital.staff("nurse"),
            )
            assert linked.status_code == 200, linked.text
            retired = client.get(
                "/patients/lookup",
                params={"opd_number": duplicate["opd_card"]["number"]},
                headers=hospital.staff("nurse"),
            )
            assert retired.json()["items"] == []
            history = client.get(
                f"/visits/{again.visit_id}/history", headers=hospital.staff("doctor")
            )
            assert [visit["visit_id"] for visit in history.json()["previous_visits"]] == [
                first.visit_id
            ]
            assert history.json()["opd_number"] == original["opd_card"]["number"]

            # Two records that both have visits are never merged from here.
            _, other = _first_visit(client, kiosk, age=30, sex="female")
            refused = client.post(
                f"/queue/{first.visit_id}/link-patient",
                json={"opd_number": other["opd_card"]["number"]},
                headers=hospital.staff("nurse"),
            )
            assert refused.status_code == 409
