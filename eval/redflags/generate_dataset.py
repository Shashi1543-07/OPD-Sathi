"""Generate a deterministic, fully synthetic red-flag evaluation corpus."""

import json
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).with_name("scripts.jsonl")
RULES = [
    "RF_DS_TILE",
    "RF_CHEST_PAIN_ASSOC",
    "RF_CHEST_PAIN",
    "RF_BREATH_SEVERE",
    "RF_STROKE_SIGNS",
    "RF_BLEEDING_HEAVY",
    "RF_PREGNANCY_DANGER",
    "RF_CHILD_FEVER",
    "RF_CHILD_FEVER_ASSOC",
    "RF_SEIZURE_FAINT",
    "RF_SELF_HARM",
    "RF_TRAUMA_MAJOR",
    "RF_ALLERGY_SEVERE",
    "RF_POISON_BITE",
    "RF_SEVERE_PAIN",
    "RF_HIGH_FEVER",
]
COUNTS = {"en": 200, "hi": 150, "mr": 100, "hinglish": 50}
TEST_COUNTS = {"en": 60, "hi": 45, "mr": 30, "hinglish": 15}
MUST_COUNTS = {"en": 24, "hi": 18, "mr": 12, "hinglish": 6}
NEG_COUNTS = {"en": 20, "hi": 15, "mr": 10, "hinglish": 5}
BENIGN_COUNTS = {"en": 12, "hi": 9, "mr": 6, "hinglish": 3}

POSITIVE: dict[str, dict[str, tuple[str, dict[str, Any], list[str]]]] = {
    "RF_DS_TILE": {
        "en": ("", {"tile_ids": ["DS_CHEST"]}, ["RF_DS_TILE"]),
        "hi": ("", {"tile_ids": ["DS_BREATH"]}, ["RF_DS_TILE"]),
        "mr": ("", {"tile_ids": ["DS_STROKE"]}, ["RF_DS_TILE"]),
        "hinglish": ("", {"tile_ids": ["DS_POISON"]}, ["RF_DS_TILE"]),
    },
    "RF_CHEST_PAIN_ASSOC": {
        "en": ("chest pain and sweating", {}, ["RF_CHEST_PAIN_ASSOC", "RF_CHEST_PAIN"]),
        "hi": ("सीने में दर्द और पसीना", {}, ["RF_CHEST_PAIN_ASSOC", "RF_CHEST_PAIN"]),
        "mr": ("छातीत दुखणे आणि घाम", {}, ["RF_CHEST_PAIN_ASSOC", "RF_CHEST_PAIN"]),
        "hinglish": ("seene mein dard aur pasina", {}, ["RF_CHEST_PAIN_ASSOC", "RF_CHEST_PAIN"]),
    },
    "RF_CHEST_PAIN": {
        "en": ("chest pain", {}, ["RF_CHEST_PAIN"]),
        "hi": ("सीने में दर्द", {}, ["RF_CHEST_PAIN"]),
        "mr": ("छातीत दुखणे", {}, ["RF_CHEST_PAIN"]),
        "hinglish": ("chest me dard", {}, ["RF_CHEST_PAIN"]),
    },
    "RF_BREATH_SEVERE": {
        "en": ("cannot breathe", {}, ["RF_BREATH_SEVERE"]),
        "hi": ("सांस नहीं ले पा रहा", {}, ["RF_BREATH_SEVERE"]),
        "mr": ("श्वास घेता येत नाही", {}, ["RF_BREATH_SEVERE"]),
        "hinglish": ("saans nahi aa rahi", {}, ["RF_BREATH_SEVERE"]),
    },
    "RF_STROKE_SIGNS": {
        "en": ("face drooping", {}, ["RF_STROKE_SIGNS"]),
        "hi": ("एक तरफ कमजोरी", {}, ["RF_STROKE_SIGNS"]),
        "mr": ("चेहरा वाकडा", {}, ["RF_STROKE_SIGNS"]),
        "hinglish": ("chehra tedha", {}, ["RF_STROKE_SIGNS"]),
    },
    "RF_BLEEDING_HEAVY": {
        "en": ("heavy bleeding", {}, ["RF_BLEEDING_HEAVY"]),
        "hi": ("खून की उल्टी", {}, ["RF_BLEEDING_HEAVY"]),
        "mr": ("शौचात रक्त", {}, ["RF_BLEEDING_HEAVY"]),
        "hinglish": ("bahut khoon behna", {}, ["RF_BLEEDING_HEAVY"]),
    },
    "RF_PREGNANCY_DANGER": {
        "en": ("vaginal bleeding", {"pregnant": True}, ["RF_PREGNANCY_DANGER"]),
        "hi": ("पेट में तेज दर्द", {"pregnant": True}, ["RF_PREGNANCY_DANGER"]),
        "mr": ("योनीतून रक्तस्राव", {"pregnant": True}, ["RF_PREGNANCY_DANGER"]),
        "hinglish": ("pregnancy me bleeding", {"pregnant": True}, ["RF_PREGNANCY_DANGER"]),
    },
    "RF_CHILD_FEVER": {
        "en": ("fever", {"age": 0}, ["RF_CHILD_FEVER"]),
        "hi": ("बुखार", {"age": 0}, ["RF_CHILD_FEVER"]),
        "mr": ("ताप", {"age": 0}, ["RF_CHILD_FEVER"]),
        "hinglish": ("bukhar", {"age": 0}, ["RF_CHILD_FEVER"]),
    },
    "RF_CHILD_FEVER_ASSOC": {
        "en": (
            "fever and fits",
            {"age": 4},
            ["RF_CHILD_FEVER_ASSOC", "RF_SEIZURE_FAINT"],
        ),
        "hi": ("बुखार और खाना नहीं खा रहा", {"age": 3}, ["RF_CHILD_FEVER_ASSOC"]),
        "mr": ("ताप आणि खूप सुस्त", {"age": 2}, ["RF_CHILD_FEVER_ASSOC"]),
        "hinglish": (
            "bukhar aur daura",
            {"age": 4},
            ["RF_CHILD_FEVER_ASSOC", "RF_SEIZURE_FAINT"],
        ),
    },
    "RF_SEIZURE_FAINT": {
        "en": ("seizure", {}, ["RF_SEIZURE_FAINT"]),
        "hi": ("दौरा", {}, ["RF_SEIZURE_FAINT"]),
        "mr": ("झटका", {}, ["RF_SEIZURE_FAINT"]),
        "hinglish": ("daura", {}, ["RF_SEIZURE_FAINT"]),
    },
    "RF_SELF_HARM": {
        "en": ("I want to die", {}, ["RF_SELF_HARM"]),
        "hi": ("खुद को नुकसान", {}, ["RF_SELF_HARM"]),
        "mr": ("स्वतःला इजा", {}, ["RF_SELF_HARM"]),
        "hinglish": ("khud ko nuksan", {}, ["RF_SELF_HARM"]),
    },
    "RF_TRAUMA_MAJOR": {
        "en": ("road accident", {}, ["RF_TRAUMA_MAJOR"]),
        "hi": ("ऊंचाई से गिरा", {}, ["RF_TRAUMA_MAJOR"]),
        "mr": ("रस्ते अपघात", {}, ["RF_TRAUMA_MAJOR"]),
        "hinglish": ("road accident", {}, ["RF_TRAUMA_MAJOR"]),
    },
    "RF_ALLERGY_SEVERE": {
        "en": ("hives with breathing trouble", {}, ["RF_ALLERGY_SEVERE"]),
        "hi": ("चेहरे की सूजन और सांस फूलना", {}, ["RF_ALLERGY_SEVERE"]),
        "mr": ("घशाला सूज आणि श्वासाचा त्रास", {}, ["RF_ALLERGY_SEVERE"]),
        "hinglish": ("gale me sujan aur saans ki dikkat", {}, ["RF_ALLERGY_SEVERE"]),
    },
    "RF_POISON_BITE": {
        "en": ("snake bite", {}, ["RF_POISON_BITE"]),
        "hi": ("जहर पी लिया", {}, ["RF_POISON_BITE"]),
        "mr": ("विंचू चावला", {}, ["RF_POISON_BITE"]),
        "hinglish": ("zeher pi liya", {}, ["RF_POISON_BITE"]),
    },
    "RF_SEVERE_PAIN": {
        "en": ("severe pain", {"severity": 9}, ["RF_SEVERE_PAIN"]),
        "hi": ("तेज दर्द", {"severity": 9}, ["RF_SEVERE_PAIN"]),
        "mr": ("तीव्र वेदना", {"severity": 9}, ["RF_SEVERE_PAIN"]),
        "hinglish": ("bahut tez dard", {"severity": 9}, ["RF_SEVERE_PAIN"]),
    },
    "RF_HIGH_FEVER": {
        "en": ("fever 104", {}, ["RF_HIGH_FEVER"]),
        "hi": ("१०४ बुखार", {}, ["RF_HIGH_FEVER"]),
        "mr": ("१०४ ताप", {}, ["RF_HIGH_FEVER"]),
        "hinglish": ("104 fever", {}, ["RF_HIGH_FEVER"]),
    },
}
NEGATIVE = {
    "en": [
        "no chest pain",
        "chest pain but no sweating",
        "no seizure",
        "no fever",
        "without breathlessness",
    ],
    "hi": ["सीने में दर्द नहीं", "सीने में दर्द लेकिन पसीना नहीं", "दौरा नहीं", "बुखार नहीं", "सांस में तकलीफ नहीं"],
    "mr": ["छातीत दुखत नाही", "छातीत दुखणे पण घाम नाही", "झटका नाही", "ताप नाही", "श्वासाचा त्रास नाही"],
    "hinglish": [
        "chest pain nahi",
        "chest pain but pasina nahi",
        "daura nahi",
        "bukhar nahi",
        "saans ki dikkat nahi",
    ],
}
BENIGN = {
    "en": [
        "mild tiredness",
        "itchy skin",
        "old knee stiffness",
        "runny nose",
        "mild back ache",
        "routine check",
    ],
    "hi": [
        "थोड़ी थकान",
        "त्वचा में खुजली",
        "पुराना घुटने का दर्द",
        "नाक बहना",
        "हल्का पीठ दर्द",
        "सामान्य जांच",
    ],
    "mr": ["थोडा थकवा", "त्वचेची खाज", "जुनाट गुडघेदुखी", "नाक वाहणे", "हलकी पाठदुखी", "नियमित तपासणी"],
    "hinglish": [
        "thodi thakan",
        "skin me khujli",
        "purana knee pain",
        "naak behna",
        "halka back pain",
        "routine check",
    ],
}


def _record(
    *,
    language: str,
    text: str,
    facts: dict[str, Any],
    labels: list[str],
    split: str,
    must_catch: bool = False,
    kind: str,
) -> dict[str, Any]:
    return {
        "id": f"RF-{split}-{language}-{kind}-{_record.index:04d}",
        "language": language,
        "text": text,
        "facts": facts,
        "expected": labels,
        "split": split,
        "must_catch": must_catch,
        "kind": kind,
    }


_record.index = 0  # type: ignore[attr-defined]


def make_record(**kwargs: Any) -> dict[str, Any]:
    _record.index += 1  # type: ignore[attr-defined]
    return _record(**kwargs)


def generate() -> list[dict[str, Any]]:
    test: list[dict[str, Any]] = []
    dev: list[dict[str, Any]] = []
    # Gate cases include all 60 must-catch, 50 negation/near-miss, and 30 benign cases.
    for language in COUNTS:
        for index in range(MUST_COUNTS[language]):
            rule_id = RULES[(index * 7 + list(COUNTS).index(language) * 3) % len(RULES)]
            text, facts, labels = POSITIVE[rule_id][language]
            test.append(
                make_record(
                    language=language,
                    text=text,
                    facts=facts,
                    labels=labels,
                    split="test",
                    must_catch=True,
                    kind="must",
                )
            )
        for index in range(NEG_COUNTS[language]):
            text = NEGATIVE[language][index % len(NEGATIVE[language])]
            labels = ["RF_CHEST_PAIN"] if index % len(NEGATIVE[language]) == 1 else []
            test.append(
                make_record(
                    language=language, text=text, facts={}, labels=labels, split="test", kind="negation"
                )
            )
        for index in range(BENIGN_COUNTS[language]):
            text = BENIGN[language][index % len(BENIGN[language])]
            test.append(
                make_record(
                    language=language, text=text, facts={}, labels=[], split="test", kind="benign"
                )
            )
        extras = (
            TEST_COUNTS[language]
            - MUST_COUNTS[language]
            - NEG_COUNTS[language]
            - BENIGN_COUNTS[language]
        )
        for index in range(extras):
            rule_id = RULES[(index * 5 + 4) % len(RULES)]
            text, facts, labels = POSITIVE[rule_id][language]
            test.append(
                make_record(
                    language=language,
                    text=text,
                    facts=facts,
                    labels=labels,
                    split="test",
                    kind="positive",
                )
            )
        dev_total = COUNTS[language] - TEST_COUNTS[language]
        for index in range(dev_total):
            if index % 4 == 0:
                text = BENIGN[language][index % len(BENIGN[language])]
                facts, labels, kind = {}, [], "benign"
            elif index % 4 == 1:
                text = NEGATIVE[language][index % len(NEGATIVE[language])]
                labels = ["RF_CHEST_PAIN"] if index % len(NEGATIVE[language]) == 1 else []
                facts, kind = {}, "negation"
            else:
                rule_id = RULES[(index * 11 + list(COUNTS).index(language)) % len(RULES)]
                text, facts, labels = POSITIVE[rule_id][language]
                kind = "positive"
            dev.append(
                make_record(
                    language=language, text=text, facts=facts, labels=labels, split="dev", kind=kind
                )
            )
    return test + dev


if __name__ == "__main__":
    rows = generate()
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    print(f"Wrote {len(rows)} synthetic scripts to {OUTPUT}")
