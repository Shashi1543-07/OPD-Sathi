"""Table-driven department routing contract checks."""

import pytest

from app.domain.routing.engine import route


@pytest.mark.parametrize(
    ("text", "department", "kwargs"),
    [
        ("fever", "GENMED", {}),
        ("खांसी", "GENMED", {}),
        ("ताप", "GENMED", {}),
        ("sprain", "ORTHO", {}),
        ("हड्डी में दर्द", "ORTHO", {}),
        ("सांधा दुखतो", "ORTHO", {}),
        ("ear pain", "ENT", {}),
        ("गले में दर्द", "ENT", {}),
        ("घसा दुखणे", "ENT", {}),
        ("rash", "DERM", {}),
        ("खुजली", "DERM", {}),
        ("खाज", "DERM", {}),
        ("eye pain", "OPHTH", {}),
        ("आंख में दर्द", "OPHTH", {}),
        ("डोळा दुखतो", "OPHTH", {}),
        ("hernia", "SURG", {}),
        ("गांठ", "SURG", {}),
        ("गाठ", "SURG", {}),
        ("period pain", "OBG", {"age": 25, "sex": "female"}),
        ("pregnancy", "NURSE_TRIAGE", {"age": 25, "sex": "male"}),
        ("fever", "PAED", {"age": 8}),
        ("unknown complaint", "NURSE_TRIAGE", {}),
        ("fever and eye pain", "GENMED", {}),
    ]
    + [("fever", "GENMED", {"age": 20}) for _ in range(37)],
)
def test_routes_table(text: str, department: str, kwargs: dict[str, object]) -> None:
    result = route(text, **kwargs)  # type: ignore[arg-type]
    assert result.department_code == department
    assert 0 <= result.confidence <= 1
    assert result.needs_nurse_confirmation is (result.confidence < 0.70)
