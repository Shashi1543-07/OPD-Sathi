"""Generate a synthetic multilingual routing evaluation set."""

import json
from pathlib import Path

OUTPUT = Path(__file__).with_name("cases.jsonl")
PHRASES = {
    "GENMED": {
        "en": "fever and cough", "hi": "बुखार और खांसी", "mr": "ताप आणि खोकला", "hinglish": "bukhar aur khansi",
    },
    "ORTHO": {
        "en": "joint pain after sprain", "hi": "हड्डी में दर्द", "mr": "सांधा दुखणे", "hinglish": "joint me dard",
    },
    "ENT": {
        "en": "ear pain", "hi": "गले में दर्द", "mr": "कान दुखणे", "hinglish": "kan me dard",
    },
    "OBG": {
        "en": "period pain", "hi": "मासिक दर्द", "mr": "पाळीची वेदना", "hinglish": "period pain",
    },
    "DERM": {
        "en": "itchy skin rash", "hi": "त्वचा में खुजली", "mr": "त्वचेवर पुरळ", "hinglish": "skin me khujli",
    },
    "OPHTH": {
        "en": "blurred vision", "hi": "आंख में दर्द", "mr": "डोळा दुखणे", "hinglish": "aankh me dard",
    },
    "SURG": {
        "en": "abdominal lump", "hi": "पेट में गांठ", "mr": "पोटात गाठ", "hinglish": "pet me ganth",
    },
}
LANGUAGES = ("en", "hi", "mr", "hinglish")


def generate() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for code, phrases in PHRASES.items():
        for language in LANGUAGES:
            for repeat in range(10):
                facts: dict[str, object] = {}
                if code == "OBG":
                    facts = {"age": 25, "sex": "female"}
                if code == "GENMED" and repeat == 7:
                    facts = {"age": 8}
                    expected = "PAED"
                elif code == "ORTHO" and repeat == 7:
                    facts = {"age": 8}
                    expected = "PAED"
                else:
                    expected = code
                rows.append({
                    "id": f"ROUTE-{code}-{language}-{repeat:02d}",
                    "language": language,
                    "text": phrases[language],
                    "facts": facts,
                    "expected": expected,
                    "split": "test" if repeat in {0, 1, 2} else "dev",
                })
    for language in LANGUAGES:
        for repeat in range(10):
            rows.append({
                "id": f"ROUTE-NURSE_TRIAGE-{language}-{repeat:02d}",
                "language": language,
                "text": {"en": "unclear concern", "hi": "अन्य समस्या", "mr": "इतर त्रास", "hinglish": "dusri dikkat"}[language],
                "facts": {}, "expected": "NURSE_TRIAGE",
                "split": "test" if repeat < 3 else "dev",
            })
    return rows


if __name__ == "__main__":
    rows = generate()
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"Wrote {len(rows)} routing cases to {OUTPUT}")
