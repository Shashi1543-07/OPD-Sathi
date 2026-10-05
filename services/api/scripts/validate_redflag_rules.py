"""Fail CI when a draft red-flag rule is missing usable evaluation examples."""

from pathlib import Path

import yaml

from app.domain.redflags.engine import PatientFacts, evaluate, load_rules

EXAMPLES = Path(__file__).parents[3] / "eval" / "redflags" / "unit_examples.yaml"


def validate() -> dict[str, int]:
    rules = load_rules()
    examples = yaml.safe_load(EXAMPLES.read_text(encoding="utf-8"))
    if not isinstance(examples, dict) or set(examples) != {rule.id for rule in rules}:
        raise ValueError("every red-flag rule must have a unit_examples.yaml entry")
    positives = negatives = 0
    for rule in rules:
        entry = examples[rule.id]
        if len(entry.get("positive", [])) < 3 or len(entry.get("negative", [])) < 2:
            raise ValueError(f"{rule.id} needs at least 3 positive and 2 negative examples")
        for positive in entry["positive"]:
            facts = PatientFacts(**positive.get("facts", {}))
            if rule.id not in {flag.id for flag in evaluate(positive["text"], facts=facts)}:
                raise ValueError(f"{rule.id} does not match a positive unit example")
        for negative in entry["negative"]:
            facts = PatientFacts(**negative.get("facts", {}))
            if rule.id in {flag.id for flag in evaluate(negative["text"], facts=facts)}:
                raise ValueError(f"{rule.id} incorrectly matches a negative unit example")
        positives += len(entry["positive"])
        negatives += len(entry["negative"])
    return {"rules": len(rules), "positive_examples": positives, "negative_examples": negatives}


if __name__ == "__main__":
    print(validate())
