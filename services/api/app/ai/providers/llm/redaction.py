"""Deterministic transcript PII redaction before any external model request."""

import re

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])",
    re.I,
)
_ABHA = re.compile(r"(?<!\d)(?:\d[ \t-]?){13}\d(?!\d)")
_IDENTIFIER_12 = re.compile(r"(?<!\d)(?:\d[ \t-]?){11}\d(?!\d)")
_COUNTRY_PHONE = re.compile(
    r"(?<!\w)(?:\+91|0091|91)[\s().-]*\(?"
    r"(?:0[\s().-]*)?[6-9](?:[\s().-]*\d){9}\)?"
    r"(?!\w)(?![\s().-]*\d)"
)
_PHONE = re.compile(r"(?<!\w)\(?0?[6-9](?:[\s().-]*\d){9}\)?(?!\w)(?![\s().-]*\d)")


def redact_pii(text: str) -> str:
    """Replace phone numbers, 14/12-digit IDs, and email addresses in transcript text.

    The function deliberately has no name parameter: names are collected in a separate
    touch-screen flow and must not be joined to patient transcript prompts.
    """
    redacted = _EMAIL.sub("[REDACTED_EMAIL]", text)
    redacted = _COUNTRY_PHONE.sub("[REDACTED_PHONE]", redacted)
    redacted = _ABHA.sub("[REDACTED_ABHA]", redacted)
    redacted = _IDENTIFIER_12.sub("[REDACTED_ID]", redacted)
    return _PHONE.sub("[REDACTED_PHONE]", redacted)
