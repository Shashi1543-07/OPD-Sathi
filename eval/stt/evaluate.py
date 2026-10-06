"""Run the deterministic text-only STT fixture evaluation used by CI."""

import asyncio
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.ai.providers.stt.mock import MockSTT

FIXTURES = Path(__file__).with_name("fixtures.jsonl")
_LANGUAGES = {"hi-IN": "Hindi", "mr-IN": "Marathi", "en-IN": "English"}


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Compute word error rate from Unicode whitespace-delimited tokens."""
    expected = reference.split()
    actual = hypothesis.split()
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for row, expected_word in enumerate(expected, start=1):
        current = [row]
        for column, actual_word in enumerate(actual, start=1):
            current.append(
                min(
                    current[column - 1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected_word != actual_word),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def keyword_recall(hypothesis: str, keywords: list[str]) -> float:
    """Measure exact token recall for fixture keywords, case-insensitively."""
    if not keywords:
        return 1.0
    actual = {token.casefold() for token in hypothesis.split()}
    return sum(keyword.casefold() in actual for keyword in keywords) / len(keywords)


def percentile95(values: list[float]) -> float:
    if not values:
        return 0.0
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


async def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure adapter plumbing against explicit synthetic transcript fixtures."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["language"]].append(row)

    output: dict[str, Any] = {}
    for language, language_rows in sorted(grouped.items()):
        errors: list[float] = []
        recalls: list[float] = []
        latencies: list[float] = []
        for row in language_rows:
            provider = MockSTT(transcripts={language: [row["text"]]})
            started = time.perf_counter()
            result = await provider.transcribe_clip(b"synthetic-text-fixture", language)
            latencies.append((time.perf_counter() - started) * 1000)
            errors.append(word_error_rate(row["text"], result.text))
            recalls.append(keyword_recall(result.text, row["keywords"]))
        output[language] = {
            "language": _LANGUAGES.get(language, language),
            "cases": len(language_rows),
            "word_error_rate": statistics.mean(errors) if errors else 0.0,
            "keyword_recall": statistics.mean(recalls) if recalls else 0.0,
            "mock_latency_ms_p95": percentile95(latencies),
        }

    return {
        "evaluation": "synthetic text-only MockSTT adapter check",
        "live_audio_evaluated": False,
        "cases": len(rows),
        "languages": output,
    }


async def main() -> None:
    rows = [
        json.loads(line)
        for line in FIXTURES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(json.dumps(await evaluate(rows), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
