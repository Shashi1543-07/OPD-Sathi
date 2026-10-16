from uuid import uuid4

import pytest

from app.domain.patients import (
    card_pin,
    demographics_consistent,
    format_mrn,
    normalize_mrn,
    pin_matches,
)

KEY = b"k" * 32


def test_registration_numbers_are_formatted_and_typed_forms_are_accepted() -> None:
    assert format_mrn(2026, 123) == "26-000123"
    assert format_mrn(2031, 1_234_567) == "31-1234567"
    for typed in ("26-000123", "26 000123", "26000123", " 26/000123 "):
        assert normalize_mrn(typed) == "26-000123"
    for wrong in ("", "26-12", "abc-000123", "2026-000123", "26-0001234567"):
        assert normalize_mrn(wrong) is None
    with pytest.raises(ValueError):
        format_mrn(2026, 0)


def test_card_pins_are_six_digits_stable_and_change_with_the_card_version() -> None:
    hospital, patient = uuid4(), uuid4()
    pin = card_pin(key=KEY, hospital_id=hospital, patient_id=patient, version=1)
    assert len(pin) == 6 and pin.isdigit()
    assert pin == card_pin(key=KEY, hospital_id=hospital, patient_id=patient, version=1)
    assert pin != card_pin(key=KEY, hospital_id=hospital, patient_id=patient, version=2)
    assert pin_matches(f" {pin[:3]} {pin[3:]} ", pin)
    assert not pin_matches("000000" if pin != "000000" else "111111", pin)
    with pytest.raises(ValueError):
        card_pin(key=b"short", hospital_id=hospital, patient_id=patient, version=1)
    with pytest.raises(ValueError):
        card_pin(key=KEY, hospital_id=hospital, patient_id=patient, version=0)


@pytest.mark.parametrize(
    ("stored_year", "stored_sex", "age", "sex", "expected"),
    [
        (1986, "female", 40, "female", True),  # exact
        (1986, "female", 39, "female", True),  # birthday not reached yet
        (1986, "female", 41, "female", True),  # within a year
        (1986, "female", 45, "female", False),  # five years apart
        (1986, "female", 40, "male", False),  # different recorded sex
        (1986, "other", 40, "male", True),  # "other" never blocks
        (None, "unknown", 40, "female", True),  # nothing recorded
        (1986, "female", None, None, True),  # nothing given today
    ],
)
def test_demographics_must_fit_the_record_but_unknowns_never_block(
    stored_year: int | None, stored_sex: str, age: int | None, sex: str | None, expected: bool
) -> None:
    assert (
        demographics_consistent(
            stored_birth_year=stored_year,
            stored_sex=stored_sex,
            age=age,
            sex=sex,
            current_year=2026,
        )
        is expected
    )
