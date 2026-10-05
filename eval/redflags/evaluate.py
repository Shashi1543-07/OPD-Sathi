"""Evaluate the deterministic red-flag rules against labelled synthetic scripts."""

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.domain.redflags.engine import PatientFacts, evaluate

DATA = Path(__file__).with_name("scripts.jsonl")


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    must_total = must_caught = 0
    neg_total = neg_correct = 0
    by_rule: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for row in rows:
        facts = PatientFacts(**row.get("facts", {}))
        actual = {flag.id for flag in evaluate(row["text"], facts=facts)}
        expected = set(row["expected"])
        tp += len(actual & expected)
        fp += len(actual - expected)
        fn += len(expected - actual)
        tn += int(not actual and not expected)
        if row.get("must_catch"):
            must_total += 1
            must_caught += int(bool(actual & expected))
        if row["kind"] == "negation":
            neg_total += 1
            neg_correct += int(actual == expected)
        for rule_id in actual | expected:
            by_rule[rule_id]["tp"] += int(rule_id in actual & expected)
            by_rule[rule_id]["fp"] += int(rule_id in actual - expected)
            by_rule[rule_id]["fn"] += int(rule_id in expected - actual)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "scripts": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "must_catch": {"caught": must_caught, "total": must_total},
        "negation": {"correct": neg_correct, "total": neg_total},
        "per_rule": {
            key: {
                **value,
                "recall": value["tp"] / (value["tp"] + value["fn"])
                if value["tp"] + value["fn"]
                else 1.0,
                "precision": value["tp"] / (value["tp"] + value["fp"])
                if value["tp"] + value["fp"]
                else 1.0,
            }
            for key, value in sorted(by_rule.items())
        },
    }


def main() -> None:
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line]
    profile = {
        "scripts": len(rows),
        "languages": dict(sorted(Counter(row["language"] for row in rows).items())),
        "splits": dict(sorted(Counter(row["split"] for row in rows).items())),
        "test_kinds": dict(
            sorted(Counter(row["kind"] for row in rows if row["split"] == "test").items())
        ),
    }
    summary: dict[str, Any] = {}
    for split in ("dev", "test"):
        result = score([row for row in rows if row["split"] == split])
        summary[split] = result
        print(f"{split}: {json.dumps(result, ensure_ascii=False, sort_keys=True)}")
    summary["dataset_profile"] = profile
    summary["misses"] = []
    for row in rows:
        actual = {
            flag.id for flag in evaluate(row["text"], facts=PatientFacts(**row.get("facts", {})))
        }
        if set(row["expected"]) - actual:
            summary["misses"].append(
                {"id": row["id"], "expected": row["expected"], "actual": sorted(actual)}
            )
    report = Path(__file__).parents[2] / "docs" / "EVALUATION.md"
    routing_path = Path(__file__).parents[1] / "routing" / "evaluation.json"
    routing = json.loads(routing_path.read_text(encoding="utf-8")) if routing_path.exists() else {}
    report.write_text(
        "# Synthetic evaluation results\n\n"
        "> Draft rule and routing content has not received clinical or native-speaker review. "
        "These synthetic cases are generated from the same phrase vocabulary as the rules, so "
        "scores are implementation checks and do not estimate real-world clinical performance.\n\n"
        "## Red flags\n\n```json\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        "## Department routing\n\n```json\n"
        + json.dumps(routing, ensure_ascii=False, indent=2)
        + "\n```\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
