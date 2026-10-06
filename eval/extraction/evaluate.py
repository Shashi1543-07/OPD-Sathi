"""Exercise extraction validation and merging with synthetic MockLLM gold fixtures."""

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.ai.extraction import extract_case_sheet_patch
from app.ai.providers.llm.mock import MockLLM
from app.core.logging import configure_logging

DATA = Path(__file__).with_name("dialogues.jsonl")


def empty_sheet(language: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "language": language,
        "chief_complaint": {"text_original": "pain", "text_en": "pain", "confidence": 0.8},
        "hpi": {},
        "routing": {
            "department_code": "GENERAL",
            "confidence": 0.8,
            "reason_en": "Synthetic evaluation fixture.",
            "needs_nurse_confirmation": False,
        },
    }


async def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = 0
    by_language: dict[str, Counter[str]] = {}
    for row in rows:
        provider = MockLLM([{"findings": row["gold"]}])
        updated = await extract_case_sheet_patch(
            provider,
            empty_sheet(row["language"]),
            row["transcript"],
            row["language"],
            session_id=row["id"],
        )
        actual: set[tuple[str, str]] = set()
        for path in (
            "/hpi/onset",
            "/hpi/location",
            "/hpi/character",
            "/hpi/radiation",
            "/hpi/timing",
        ):
            section, field = path.split("/")[1:]
            value = updated[section].get(field)
            if value:
                actual.add((path, value["text_original"]))
        for path in (
            "/hpi/associated_symptoms",
            "/hpi/pertinent_negatives",
            "/hpi/aggravating",
            "/hpi/relieving",
        ):
            section, field = path.split("/")[1:]
            actual.update(
                (path, item) for item in (x["text_original"] for x in updated[section][field])
            )
        expected = {(item["path"], item["text_original"]) for item in row["gold"]}
        tp += len(actual & expected)
        fp += len(actual - expected)
        fn += len(expected - actual)
        counts = by_language.setdefault(row["language"], Counter())
        counts.update(
            tp=len(actual & expected), fp=len(actual - expected), fn=len(expected - actual)
        )
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "fixtures": len(rows),
        "field_counts": {"tp": tp, "fp": fp, "fn": fn},
        "field_precision": precision,
        "field_recall": recall,
        "field_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
        "unsupported_fields": fp,
        "languages": {key: dict(value) for key, value in sorted(by_language.items())},
        "interpretation": (
            "MockLLM returns gold fixtures; this validates pipeline handling, not model quality."
        ),
    }


def main() -> None:
    configure_logging("ERROR")
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line]
    profile = dict(sorted(Counter(row["language"] for row in rows).items()))
    summary = {"dataset_profile": profile, **asyncio.run(evaluate(rows))}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
