"""PII redaction tests use synthetic identifiers only."""

import pytest

from app.ai.providers.llm.redaction import redact_pii

PHONE_CASES = [
    "9876543210",
    "98765 43210",
    "98765-43210",
    "987.654.3210",
    "9 8 7 6 5 4 3 2 1 0",
    "+91 9876543210",
    "+91-9876543210",
    "+91 (98765) 43210",
    "0091 9876543210",
    "91 9876543210",
    "09876543210",
    "(98765) 43210",
    "+91.98765.43210",
    "+91 98765-43210",
    "9876 543 210",
]

ABHA_CASES = [
    "12345678901234",
    "12-3456-7890-1234",
    "12 3456 7890 1234",
    "1234-5678-9012-34",
    "1234 5678 9012 34",
    "123-456-789-01234",
    "1-2-3-4-5-6-7-8-9-0-1-2-3-4",
    "12 34 56 78 90 12 34",
    "123456-78901234",
    "12345678-901234",
    "1234567890 1234",
    "1234 5678901234",
]

IDENTIFIER_CASES = [
    "234567890123",
    "2345 6789 0123",
    "2345-6789-0123",
    "2-3-4-5-6-7-8-9-0-1-2-3",
    "234 567 890 123",
    "234567 890123",
    "23456789 0123",
    "2345 67890123",
    "23-4567890123",
    "234567890-123",
    "234 567890123",
    "234567890 123",
]

EMAIL_CASES = [
    "person@example.com",
    "patient.one@example.in",
    "a+b@example.co.in",
    "user_2@sub.example.org",
    "first-last@hospital.example",
    "UPPER.CASE@EXAMPLE.COM",
    "name%tag@example.net",
    "x@y.io",
    "foo.bar+clinic@gmail.com",
    "name!#$%&'*+/=?^_`{|}~-@example.com",
    "contact@xn--example-ova.test",
    "patient123@clinic.example.in",
]

NON_PII_NUMBERS = [
    "I am 25 years old.",
    "Temperature is 38.5 C.",
    "Take 500 mg as previously prescribed.",
    "Pain started in 2024 and got worse in 2025.",
    "My appointment is on 2026-10-06.",
    "There are 3 symptoms and 2 days of fever.",
    "Room 1204, counter 7.",
    "BP was 120/80.",
    "The value is 12345678901.",
    "The value is 1234567890123.",
    "Call extension 1234567890?",  # Leading 1 is not an Indian mobile number.
    "Reference 1234-567-8901 has eleven digits.",
    "Dose is 1.25 ml.",
    "My height is 172 cm and weight is 68 kg.",
    "Code 0000000000 is not a mobile number.",
    "Reference +91 123456789 is not a complete mobile number.",
    "The result was 6 out of 10.",
    "Patient waited 45 minutes.",
    "The clinic is open from 09:00 to 17:00.",
    "The report contains 14 pages and 12 tables.",
]


@pytest.mark.parametrize("value", PHONE_CASES)
def test_redacts_phone_formats(value: str) -> None:
    assert redact_pii(value) == "[REDACTED_PHONE]"


@pytest.mark.parametrize("value", ABHA_CASES)
def test_redacts_fourteen_digit_abha_formats(value: str) -> None:
    assert redact_pii(value) == "[REDACTED_ABHA]"


@pytest.mark.parametrize("value", IDENTIFIER_CASES)
def test_redacts_twelve_digit_identifiers(value: str) -> None:
    assert redact_pii(value) == "[REDACTED_ID]"


@pytest.mark.parametrize("value", EMAIL_CASES)
def test_redacts_email_formats(value: str) -> None:
    assert redact_pii(value) == "[REDACTED_EMAIL]"


@pytest.mark.parametrize("text", NON_PII_NUMBERS)
def test_preserves_non_pii_numbers(text: str) -> None:
    assert redact_pii(text) == text


def test_preserves_surrounding_text_while_redacting_multiple_values() -> None:
    text = "I am 25; call +91 9876543210 or email user@example.com about ABHA 12-3456-7890-1234."
    assert redact_pii(text) == (
        "I am 25; call [REDACTED_PHONE] or email [REDACTED_EMAIL] about ABHA [REDACTED_ABHA]."
    )


def test_does_not_treat_a_transcript_as_a_name_source() -> None:
    assert "name" not in redact_pii.__code__.co_varnames
