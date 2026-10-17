"""Old papers: which photos are accepted, how results are flagged, and what AI may return."""

from datetime import date
from typing import Any

import pytest

from app.ai.document_reading import read_document
from app.ai.providers.llm.base import LLMUnavailableError
from app.api.v1.documents import _reading
from app.domain.documents import document_date, looks_like, printed_flag, range_flag


@pytest.mark.parametrize(
    ("value", "reference_range", "expected"),
    [
        ("9.8", "12.0 - 15.0", "below"),
        ("16.2 g/dL", "12.0-15.0", "above"),
        ("13", "12 to 15", "within"),
        ("12", "12 – 15", "within"),  # A value at a limit is within the range.
        ("15.0", "(12.0 - 15.0)", "within"),
        ("2,10,000", "1,50,000 - 4,50,000", "within"),
        ("4,80,000", "[150000 - 450000]", "above"),
        ("6.1", "< 5.7", "above"),
        ("5.7", "<= 5.7", "within"),
        ("4", "up to 5", "within"),
        ("38", "> 40", "below"),
        ("45", "≥ 40 mL/min", "within"),
        ("132 mg/dL", "70 - 100 mg/dL", "above"),
    ],
)
def test_a_result_is_compared_with_the_range_printed_beside_it(
    value: str, reference_range: str, expected: str
) -> None:
    assert range_flag(value, reference_range) == expected


@pytest.mark.parametrize(
    ("value", "reference_range"),
    [
        ("No growth", ""),  # Text results are never judged.
        ("Positive", "Negative"),
        ("1+", "Nil"),
        ("12.5", ""),  # No range printed: nothing to compare with.
        ("12.5", "Adult: 12-15, Child: 11-14"),  # Several ranges: not guessed.
        ("12.5", "15 - 12"),  # A reversed range is not trusted.
        ("< 0.5", "0 - 1"),  # A censored value is not a number.
        ("", "12 - 15"),
    ],
)
def test_nothing_is_flagged_from_a_guess(value: str, reference_range: str) -> None:
    assert range_flag(value, reference_range) is None


@pytest.mark.parametrize(
    ("printed", "expected"),
    [("H", "high"), (" low ", "low"), ("↑", "high"), ("L", "low"), ("", None), ("*", None)],
)
def test_the_laboratorys_own_mark_is_shown_as_printed(printed: str, expected: str | None) -> None:
    assert printed_flag(printed) == expected


def test_only_a_real_past_date_is_taken_from_a_paper() -> None:
    today = date(2026, 10, 17)
    assert document_date("2026-09-30", today=today) == date(2026, 9, 30)
    assert document_date(" 2026-10-17 ", today=today) == today
    for raw in ("2026-10-18", "1949-12-31", "30/09/2026", "2026-02-30", ""):
        assert document_date(raw, today=today) is None


def test_a_photo_must_be_what_it_claims_to_be() -> None:
    assert looks_like("image/jpeg", b"\xff\xd8\xff\xe0rest")
    assert looks_like("image/png", b"\x89PNG\r\n\x1a\nrest")
    assert looks_like("image/webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert not looks_like("image/jpeg", b"%PDF-1.7")
    assert not looks_like("image/png", b"\xff\xd8\xff\xe0")
    assert not looks_like("image/webp", b"RIFF\x00\x00\x00\x00AVI ")
    assert not looks_like("image/gif", b"GIF89a")


class _Provider:
    def __init__(self, output: dict[str, Any] | Exception) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def generate_json(self, parts: Any, schema: Any, **options: Any) -> dict[str, Any]:
        self.calls.append({"parts": parts, "schema": schema, **options})
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def _output(**overrides: Any) -> dict[str, Any]:
    return {
        "readable": True,
        "document_type": "prescription",
        "document_date": "2026-09-30",
        "facility": " Synthetic Clinic ",
        "diagnoses": ["Type 2 diabetes"],
        "medicines": [
            {"name": "Metformin", "dose": "500 mg", "how_often": "1-0-1", "duration": "30 days"}
        ],
        "lab_results": [],
        "procedures": [],
        **overrides,
    }


async def test_ai_copies_the_paper_into_a_checked_shape() -> None:
    provider = _Provider(_output())
    reading = await read_document(provider, b"\xff\xd8\xff", mime_type="image/jpeg", session_id="s")
    assert reading["facility"] == "Synthetic Clinic"
    assert reading["medicines"][0]["name"] == "Metformin"
    (call,) = provider.calls
    # The earlier doctor's words are copied as written, so the output guard is off; the
    # reading is only ever shown as a draft beside the photo.
    assert call["guard"] is None and call["timeout"] == 20.0 and call["session_id"] == "s"


async def test_an_unreadable_paper_comes_back_empty() -> None:
    provider = _Provider(_output(readable=False, diagnoses=["guessed"]))
    reading = await read_document(provider, b"\xff\xd8\xff", mime_type="image/jpeg", session_id="s")
    assert reading["readable"] is False
    assert reading["diagnoses"] == [] and reading["medicines"] == []


@pytest.mark.parametrize(
    "output",
    [
        _output(document_type="x-ray"),
        _output(advice="Take rest"),  # Nothing beyond the schema.
        _output(medicines=[{"name": "M", "dose": "", "how_often": "", "duration": ""}]),
        _output(diagnoses=["x"] * 11),
    ],
)
async def test_output_outside_the_shape_is_refused(output: dict[str, Any]) -> None:
    with pytest.raises(LLMUnavailableError):
        await read_document(
            _Provider(output), b"\xff\xd8\xff", mime_type="image/jpeg", session_id="s"
        )


async def test_an_unavailable_model_is_reported_as_such() -> None:
    with pytest.raises(LLMUnavailableError):
        await read_document(
            _Provider(LLMUnavailableError("down")),
            b"\xff\xd8\xff",
            mime_type="image/jpeg",
            session_id="s",
        )


def test_flags_are_added_when_the_reading_is_shown() -> None:
    shown = _reading(
        _output(
            lab_results=[
                {
                    "test": "Haemoglobin",
                    "value": "9.8",
                    "unit": "g/dL",
                    "reference_range": "12 - 15",
                    "flag": "L",
                },
                {
                    "test": "Culture",
                    "value": "No growth",
                    "unit": "",
                    "reference_range": "",
                    "flag": "",
                },
            ]
        )
    )
    assert shown is not None
    haemoglobin, culture = shown.lab_results
    assert (haemoglobin.printed_flag, haemoglobin.range_flag) == ("low", "below")
    assert (culture.printed_flag, culture.range_flag) == (None, None)
    assert _reading(None) is None
