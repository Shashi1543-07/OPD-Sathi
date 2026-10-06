"""Check deterministic fallback and monotonic urgency over synthetic red-flag cases."""

import asyncio
import json
from pathlib import Path
from typing import Any

from app.ai.providers.llm.mock import MockLLM
from app.ai.redflag_opinion import (
    combine_redflag_opinions,
    second_opinion_flags,
    urgency_never_decreases,
)
from app.core.logging import configure_logging
from app.domain.redflags.engine import PatientFacts, evaluate

DATA = Path(__file__).parents[1] / "redflags" / "scripts.jsonl"


async def run(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recall_count = must_total = must_caught = monotonic = 0
    total_expected = total_found = 0
    for row in rows:
        facts = PatientFacts(**row.get("facts", {}))
        deterministic = evaluate(row["text"], facts=facts)
        # Empty Mock opinion exercises the exact safe fallback used on unavailable output.
        provider = MockLLM([{"flags": []}])
        opinion = await second_opinion_flags(
            provider,
            transcript=row["text"] or "Synthetic symptom tile selected.",
            language=row.get("language", "en"),
            session_id=row["id"],
        )
        combined = combine_redflag_opinions(deterministic, opinion)
        actual = {flag.id for flag in combined}
        expected = set(row["expected"])
        total_expected += len(expected)
        total_found += len(actual & expected)
        recall_count += int(bool(actual & expected)) if expected else int(not actual)
        if row.get("must_catch"):
            must_total += 1
            must_caught += int(bool(actual & expected))
        monotonic += int(urgency_never_decreases(deterministic, combined))
    return {
        "synthetic_cases": len(rows),
        "expected_flags": total_expected,
        "expected_flags_found": total_found,
        "flag_recall": total_found / total_expected if total_expected else 1.0,
        "must_catch": {"caught": must_caught, "total": must_total},
        "cases_with_expected_status": recall_count,
        "urgency_monotonic_cases": monotonic,
        "interpretation": (
            "Mock emits no extra flags; measures deterministic fallback and monotonic merge, "
            "not LLM recall."
        ),
    }


def main() -> None:
    configure_logging("ERROR")
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line]
    result = asyncio.run(run(rows))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["urgency_monotonic_cases"] != len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
