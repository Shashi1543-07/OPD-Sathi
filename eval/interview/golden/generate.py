"""Regenerate the deterministic golden case sheets after reviewed schema changes."""

import json
from pathlib import Path

from app.domain.interview.engine import apply_answer, build_case_sheet, next_question, start

OUTPUT = Path(__file__).with_name("interviews.jsonl")
# The case per language asked at a hospital that adds the review of systems (task 7.11.2).
REVIEW_OF_SYSTEMS_CASE = 4
COMPLAINTS = {
    "en": [
        "fever for three days",
        "pain in my arm",
        "cough and breathing concern",
        "stomach discomfort",
        "skin rash",
        "trouble sleeping",
        "pain when walking",
        "urine discomfort",
        "fever and headache",
        "general concern",
    ],
    "hi": [
        "तीन दिन से बुखार",
        "बाँह में दर्द",
        "खाँसी और साँस की परेशानी",
        "पेट में तकलीफ़",
        "त्वचा पर खुजली",
        "नींद की परेशानी",
        "चलने में दर्द",
        "पेशाब में जलन",
        "बुखार और सिरदर्द",
        "सेहत की परेशानी",
    ],
    "mr": [
        "तीन दिवसांपासून ताप",
        "हातात दुखणे",
        "खोकला आणि श्वासाचा त्रास",
        "पोटात त्रास",
        "त्वचेवर पुरळ",
        "झोपेचा त्रास",
        "चालताना वेदना",
        "लघवी करताना जळजळ",
        "ताप आणि डोकेदुखी",
        "आरोग्याची तक्रार",
    ],
}


def main() -> None:
    cases: list[str] = []
    for language, complaints in COMPLAINTS.items():
        for index, complaint in enumerate(complaints):
            extra_modules = ("review_of_systems",) if index == REVIEW_OF_SYSTEMS_CASE else ()
            state = start(
                language,
                red_flag_ids=("chest_pain",) if index == 2 else (),
                extra_modules=extra_modules,
            )
            answers: list[dict[str, object]] = []
            while (question := next_question(state)) is not None:
                if question.id == "chief_concern":
                    answer: str | int | None = complaint
                    skip = False
                elif question.id == "duration":
                    answer = f"{index + 1} days"
                    skip = False
                elif question.input == "scale":
                    answer = index % 11
                    skip = False
                elif question.optional and index % 3 == 0:
                    answer = None
                    skip = True
                elif question.options:
                    # Tap the way a patient would: a real option label in their language.
                    choices = [option for option in question.options if not option.none]
                    picked = choices[index % len(choices)] if choices else question.options[0]
                    answer = picked.label[language]
                    skip = False
                else:
                    answer = f"Golden answer {index + 1}"
                    skip = False
                answers.append({"question_id": question.id, "answer": answer, "skip": skip})
                state = apply_answer(state, question.id, answer, skip=skip)
            cases.append(
                json.dumps(
                    {
                        "language": language,
                        "complaint": complaint,
                        "red_flag_ids": state.red_flag_ids,
                        "extra_modules": list(extra_modules),
                        "answers": answers,
                        "expected_case_sheet": build_case_sheet(state),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(cases) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} golden interviews to {OUTPUT}")


if __name__ == "__main__":
    main()
