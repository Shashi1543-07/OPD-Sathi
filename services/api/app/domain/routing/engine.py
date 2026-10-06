"""Rules-only explainable department routing; low confidence goes to nurse triage."""

from dataclasses import dataclass

from app.domain.redflags.normalize import normalize_text


@dataclass(frozen=True)
class Route:
    department_code: str
    confidence: float
    reason_en: str
    needs_nurse_confirmation: bool
    matched_modules: tuple[str, ...]


MODULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ORTHO",
        (
            "sprain",
            "fracture",
            "joint pain",
            "bone pain",
            "injury",
            "सूजन चोट",
            "हड्डी",
            "फ्रैक्चर",
            "सांधा",
            "हाड",
            "fracture zala",
            "joint dukh",
            "joint me dard",
            "haddi me dard",
            "fracture hua",
        ),
    ),
    (
        "ENT",
        (
            "ear pain",
            "hearing",
            "throat",
            "nosebleed",
            "कान दर्द",
            "गले में दर्द",
            "कान दुखणे",
            "घसा दुखणे",
            "kan dard",
            "gala dard",
            "kan me dard",
            "naak se khoon",
        ),
    ),
    (
        "OBG",
        (
            "period",
            "pregnant",
            "pregnancy",
            "vaginal bleeding",
            "गर्भवती",
            "मासिक",
            "गर्भधारणा",
            "पाळी",
            "period pain",
        ),
    ),
    (
        "DERM",
        ("rash", "itch", "skin", "खुजली", "चकत्ता", "खाज", "पुरळ", "khujli", "itching"),
    ),
    (
        "OPHTH",
        (
            "eye pain",
            "vision",
            "blurred vision",
            "आंख",
            "दृष्टि",
            "डोळा",
            "दृष्टी",
            "aankh dard",
            "aankh me dard",
            "nazar dhundli",
            "dhundla dikhna",
        ),
    ),
    (
        "SURG",
        ("hernia", "lump", "abscess", "पाइल्स", "गांठ", "हर्निया", "गाठ", "सूज", "ganth"),
    ),
    (
        "GENMED",
        (
            "fever",
            "cough",
            "chest",
            "headache",
            "stomach",
            "abdominal",
            "vomit",
            "diarrhea",
            "बुखार",
            "खांसी",
            "पेट दर्द",
            "ताप",
            "खोकला",
            "पोट दुखणे",
            "bukhar",
            "khansi",
            "pet dard",
        ),
    ),
)


def route(
    text: str,
    *,
    age: int | None = None,
    sex: str | None = None,
    pregnant: bool | None = None,
) -> Route:
    """Choose the earliest matching department and mark ambiguous cases for review."""
    normalized = normalize_text(text)
    # Prefer surgery for abdominal lumps/hernia over the broad abdominal rule.
    if any(
        term in normalized
        for term in ("abdominal lump", "stomach lump", "hernia", "पेट में गांठ", "पोटात गाठ")
    ):
        return Route(
            "SURG",
            0.90,
            "A lump or hernia term matched the surgery rule.",
            False,
            ("SURG",),
        )
    found: list[tuple[int, str, str]] = []
    for code, phrases in MODULES:
        for phrase in phrases:
            phrase_normalized = normalize_text(phrase)
            index = normalized.find(phrase_normalized)
            if index < 0:
                continue
            if code == "OBG" and not (
                (sex == "female" and age is not None and 12 <= age <= 50) or pregnant is True
            ):
                continue
            found.append((index, code, phrase))
            break
    found.sort(key=lambda item: (item[0], item[1]))
    if age is not None and age < 14 and found:
        return Route(
            "PAED",
            0.90,
            "Patient is under 14; pediatric routing is required.",
            False,
            tuple(item[1] for item in found),
        )
    if not found:
        return Route(
            "NURSE_TRIAGE",
            0.0,
            "No department rule matched; nurse confirmation is required.",
            True,
            (),
        )
    chosen = found[0][1]
    confidence = 0.90 if len({item[1] for item in found}) == 1 else 0.65
    reason = f"Matched {chosen} terms in the patient's stated concern."
    modules = tuple(dict.fromkeys(item[1] for item in found))
    return Route(chosen, confidence, reason, confidence < 0.70, modules)
