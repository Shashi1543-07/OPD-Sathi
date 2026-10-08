"""Evaluate explainable routing rules against synthetic labelled cases."""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.domain.routing.engine import route

DATA = Path(__file__).with_name("cases.jsonl")
OUTPUT = Path(__file__).with_name("evaluation.json")


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    misses: list[dict[str, str]] = []
    correct = 0
    for row in rows:
        result = route(row["text"], **row.get("facts", {}))
        expected = row["expected"]
        confusion[expected][result.department_code] += 1
        correct += int(expected == result.department_code)
        if expected != result.department_code:
            misses.append({"id": row["id"], "expected": expected, "actual": result.department_code})
    return {
        "cases": len(rows),
        "correct": correct,
        "top1_accuracy": correct / len(rows) if rows else 1.0,
        "confusion_matrix": {key: dict(sorted(value.items())) for key, value in sorted(confusion.items())},
        "misses": misses,
    }


def main(*, write_output: bool = True) -> None:
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line]
    summary = {split: score([row for row in rows if row["split"] == split]) for split in ("dev", "test")}
    if write_output:
        OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for split, result in summary.items():
        print(f"{split}: {json.dumps(result, ensure_ascii=False, sort_keys=True)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true", help="do not update evaluation.json")
    main(write_output=not parser.parse_args().no_write)
