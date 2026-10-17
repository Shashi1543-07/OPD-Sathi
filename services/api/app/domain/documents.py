"""Old papers a patient brings: what is accepted, and flags against printed reference ranges.

A result is flagged only against the reference range printed beside it on the same paper,
by plain arithmetic: never from a model's judgement and never from a range the system knows
"in general" (ranges differ between laboratories, methods, ages and sexes). A value or range
that does not parse is simply not flagged, and a high or low mark printed by the laboratory
is shown as printed.
"""

import re
from datetime import date
from typing import Literal

DocumentKind = Literal["prescription", "lab_report", "discharge_summary", "other"]
DOCUMENT_KINDS: tuple[DocumentKind, ...] = (
    "prescription",
    "lab_report",
    "discharge_summary",
    "other",
)
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
# The kiosk sends a ~1280 px JPEG (about 200 kB); the API refuses any request over 1 MiB.
MAX_IMAGE_BYTES = 1_000_000
# Photos a patient can add while checking in; staff can add more at the desk, up to the total.
MAX_PATIENT_DOCUMENTS = 5
MAX_DOCUMENTS_PER_VISIT = 10

RangeFlag = Literal["above", "below", "within"]
PrintedFlag = Literal["high", "low"]

_NUMBER = r"(\d+(?:\.\d+)?)"
_OPEN = r"\s*[(\[]?\s*"  # Ranges are often printed in brackets: "(12.0 - 15.0)".
_VALUE = re.compile(rf"\s*{_NUMBER}(?:\s*[^\d\s<>=,.].*)?")
_BETWEEN = re.compile(rf"{_OPEN}{_NUMBER}\s*(?:-|–|—|to)\s*{_NUMBER}\b.*", re.IGNORECASE)
_BELOW = re.compile(rf"{_OPEN}(?:<=?|≤|up\s*to|upto|below|less\s+than)\s*{_NUMBER}\b.*", re.I)
_ABOVE = re.compile(rf"{_OPEN}(?:>=?|≥|above|more\s+than)\s*{_NUMBER}\b.*", re.IGNORECASE)
# Indian and international digit grouping: 1,50,000 and 150,000 are both 150000.
_GROUPING = re.compile(r"(?<=\d),(?=\d)")
_PRINTED: dict[str, PrintedFlag] = {
    "h": "high",
    "high": "high",
    "↑": "high",
    "l": "low",
    "low": "low",
    "↓": "low",
}


def looks_like(content_type: str, image: bytes) -> bool:
    """Whether the file starts with the signature of the image type it claims to be."""
    if content_type == "image/jpeg":
        return image.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return image.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return image[:4] == b"RIFF" and image[8:12] == b"WEBP"
    return False


def range_flag(value: str, reference_range: str) -> RangeFlag | None:
    """Compare a printed result with the reference range printed beside it.

    Understands a plain number (optionally followed by its unit) and ranges written as
    "12 - 15", "12–15", "12 to 15", "< 5", "up to 5", "> 40". A value exactly at a limit is
    within the range. Anything else gives None: nothing is flagged from a guess.
    """
    value = _GROUPING.sub("", value)
    reference_range = _GROUPING.sub("", reference_range)
    match = _VALUE.fullmatch(value)
    if match is None:
        return None
    number = float(match.group(1))
    low: float | None = None
    high: float | None = None
    if between := _BETWEEN.fullmatch(reference_range):
        low, high = float(between.group(1)), float(between.group(2))
        if low > high:
            return None
    elif below := _BELOW.fullmatch(reference_range):
        high = float(below.group(1))
    elif above := _ABOVE.fullmatch(reference_range):
        low = float(above.group(1))
    else:
        return None
    if high is not None and number > high:
        return "above"
    if low is not None and number < low:
        return "below"
    return "within"


def printed_flag(flag: str) -> PrintedFlag | None:
    """A high or low mark as the laboratory printed it ("H", "LOW", an arrow), if any."""
    return _PRINTED.get(flag.strip().lower())


def document_date(raw: str, *, today: date) -> date | None:
    """The date printed on a paper, if it is a real calendar date that is not in the future."""
    try:
        parsed = date.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed > today or parsed.year < 1950:
        return None
    return parsed
