"""Compare the deterministic engine against the checked-in multilingual golden set."""

import json
from pathlib import Path
from typing import Any

from app.domain.interview.engine import apply_answer, build_case_sheet, next_question, start

GOLDEN_PATH = Path(__file__).with_name("golden") / "interviews.jsonl"


def evaluate() -> tuple[int, list[str]]:
    passed = 0
    errors: list[str] = []
    for line_number, line in enumerate(GOLDEN_PATH.read_text(encoding="utf-8").splitlines(), 1):
        case = json.loads(line)
        state = start(
            case["language"],
            red_flag_ids=tuple(case["red_flag_ids"]),
            extra_modules=tuple(case.get("extra_modules", ())),
        )
        for answer in case["answers"]:
            question = next_question(state)
            if question is None or question.id != answer["question_id"]:
                errors.append(f"case {line_number}: expected question {answer['question_id']}")
                break
            state = apply_answer(state, question.id, answer.get("answer"), skip=answer["skip"])
        else:
            actual: dict[str, Any] = build_case_sheet(state)
            if actual != case["expected_case_sheet"]:
                errors.append(f"case {line_number}: case sheet differs from golden output")
            else:
                passed += 1
    return passed, errors


def main() -> None:
    passed, errors = evaluate()
    print(f"Golden interview evaluation: {passed}/30 passed")
    for error in errors:
        print(error)
    if passed != 30:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
