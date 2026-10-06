"""Generate deterministic, synthetic multilingual extraction fixtures."""

import json
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).with_name("dialogues.jsonl")

PHRASES = {
    "en": [
        ("My head hurts", "/hpi/location", "head", "/hpi/character", "hurts"),
        ("My stomach hurts", "/hpi/location", "stomach", "/hpi/character", "hurts"),
        ("My back hurts", "/hpi/location", "back", "/hpi/character", "hurts"),
        ("My knee hurts", "/hpi/location", "knee", "/hpi/character", "hurts"),
        ("My chest feels tight", "/hpi/location", "chest", "/hpi/character", "tight"),
        ("I have a cough", "/hpi/associated_symptoms", "cough", "/hpi/onset", "I have"),
        ("I feel dizzy", "/hpi/associated_symptoms", "dizzy", "/hpi/onset", "I feel"),
        ("I feel nauseous", "/hpi/associated_symptoms", "nauseous", "/hpi/onset", "I feel"),
        ("My throat is sore", "/hpi/location", "throat", "/hpi/character", "sore"),
        ("My shoulder aches", "/hpi/location", "shoulder", "/hpi/character", "aches"),
    ],
    "hi": [
        ("मेरे सिर में दर्द है", "/hpi/location", "सिर", "/hpi/character", "दर्द"),
        ("मेरे पेट में दर्द है", "/hpi/location", "पेट", "/hpi/character", "दर्द"),
        ("मेरी पीठ में दर्द है", "/hpi/location", "पीठ", "/hpi/character", "दर्द"),
        ("मेरे घुटने में दर्द है", "/hpi/location", "घुटने", "/hpi/character", "दर्द"),
        ("मेरी छाती में जकड़न है", "/hpi/location", "छाती", "/hpi/character", "जकड़न"),
        ("मुझे खांसी है", "/hpi/associated_symptoms", "खांसी", "/hpi/onset", "मुझे"),
        ("मुझे चक्कर आते हैं", "/hpi/associated_symptoms", "चक्कर", "/hpi/onset", "मुझे"),
        ("मुझे मितली आती है", "/hpi/associated_symptoms", "मितली", "/hpi/onset", "मुझे"),
        ("मेरे गले में खराश है", "/hpi/location", "गले", "/hpi/character", "खराश"),
        ("मेरे कंधे में दर्द है", "/hpi/location", "कंधे", "/hpi/character", "दर्द"),
    ],
    "mr": [
        ("माझे डोके दुखते", "/hpi/location", "डोके", "/hpi/character", "दुखते"),
        ("माझे पोट दुखते", "/hpi/location", "पोट", "/hpi/character", "दुखते"),
        ("माझी पाठ दुखते", "/hpi/location", "पाठ", "/hpi/character", "दुखते"),
        ("माझा गुडघा दुखतो", "/hpi/location", "गुडघा", "/hpi/character", "दुखतो"),
        ("माझ्या छातीत घट्टपणा आहे", "/hpi/location", "छातीत", "/hpi/character", "घट्टपणा"),
        ("मला खोकला आहे", "/hpi/associated_symptoms", "खोकला", "/hpi/onset", "मला"),
        ("मला चक्कर येते", "/hpi/associated_symptoms", "चक्कर", "/hpi/onset", "मला"),
        ("मला मळमळ होते", "/hpi/associated_symptoms", "मळमळ", "/hpi/onset", "मला"),
        ("माझा घसा खवखवतो", "/hpi/location", "घसा", "/hpi/character", "खवखवतो"),
        ("माझा खांदा दुखतो", "/hpi/location", "खांदा", "/hpi/character", "दुखतो"),
    ],
}


def generate() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for language, phrases in PHRASES.items():
        count = 40 if language == "en" else 30
        for index in range(count):
            phrase, path_a, evidence_a, path_b, evidence_b = phrases[index % len(phrases)]
            # Repeated rows vary the conversational context without adding patient identifiers.
            prefix = ("Today, " if index % 2 else "") if language == "en" else ""
            transcript = f"{prefix}{phrase}" if prefix else phrase
            findings = [
                {
                    "path": path_a,
                    "text_original": evidence_a,
                    "evidence": evidence_a,
                    "confidence": 0.96,
                },
            ]
            if path_b != "/hpi/onset":
                findings.append(
                    {
                        "path": path_b,
                        "text_original": evidence_b,
                        "evidence": evidence_b,
                        "confidence": 0.93,
                    }
                )
            rows.append(
                {
                    "id": f"{language}-{index + 1:03d}",
                    "language": language,
                    "split": "dev" if index % 5 == 0 else "test",
                    "transcript": transcript,
                    "gold": findings,
                }
            )
    return rows


def main() -> None:
    rows = generate()
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    print(f"Wrote {len(rows)} synthetic extraction dialogues to {OUTPUT}")


if __name__ == "__main__":
    main()
