"""Verify STT evaluation metrics and the text-only CI fixture set."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

# The eval package lives at the repository root, outside services/api's import root.
from eval.stt.evaluate import FIXTURES, evaluate, keyword_recall, word_error_rate


def test_word_error_rate_counts_insertions_substitutions_and_deletions() -> None:
    assert word_error_rate("one two three four", "one too three") == 0.5
    assert word_error_rate("", "inserted") == 1.0


def test_keyword_recall_uses_exact_tokens_and_casefolding() -> None:
    assert keyword_recall("I have Headache and feverish", ["headache", "fever"]) == 0.5
    assert keyword_recall("", []) == 1.0


def test_mock_ci_evaluation_has_all_80_synthetic_cases() -> None:
    rows = [json.loads(line) for line in Path(FIXTURES).read_text(encoding="utf-8").splitlines()]
    result = asyncio.run(evaluate(rows))
    assert result["cases"] == 80
    assert result["live_audio_evaluated"] is False
    assert result["languages"]["hi-IN"]["cases"] == 30
    assert result["languages"]["mr-IN"]["cases"] == 30
    assert result["languages"]["en-IN"]["cases"] == 20
    assert all(
        stats["word_error_rate"] == 0 and stats["keyword_recall"] == 1
        for stats in result["languages"].values()
    )
