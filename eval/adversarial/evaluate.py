"""Measure output-guard blocks against the synthetic adversarial fixtures."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.ai.output_guard import UnsafeLLMOutputError, validate_safe_output

DATA = Path(__file__).with_name("prompts.jsonl")


def main() -> None:
    rows: list[dict[str, Any]] = [
        json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line
    ]
    blocked = 0
    missed = []
    categories: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    for row in rows:
        categories[row["category"]] += 1
        languages[row["language"]] += 1
        try:
            validate_safe_output({"text": row["unsafe_output"]})
        except UnsafeLLMOutputError:
            blocked += 1
        else:
            missed.append(row["id"])
    print(
        json.dumps(
            {
                "prompts": len(rows),
                "blocked": blocked,
                "leaks": len(missed),
                "missed_ids": missed,
                "categories": dict(sorted(categories.items())),
                "languages": dict(sorted(languages.items())),
                "interpretation": (
                    "Synthetic outputs probe the guard only; this does not measure Gemini behavior."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if missed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
