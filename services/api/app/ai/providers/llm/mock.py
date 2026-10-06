"""A fixture-driven LLM that never invents patient-specific results."""

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from app.ai.providers.llm.base import LLMMetrics, LLMUnavailableError


class MockLLM:
    """Return scripted JSON objects or inject a fixed number of failures."""

    name = "mock"

    def __init__(
        self,
        responses: Sequence[Mapping[str, Any]] = (),
        *,
        failures_before_success: int = 0,
        metrics: LLMMetrics | None = None,
    ) -> None:
        if failures_before_success < 0:
            raise ValueError("failure count cannot be negative")
        self._responses = [copy.deepcopy(dict(item)) for item in responses]
        self._failures_before_success = failures_before_success
        self.metrics = metrics or LLMMetrics()

    async def generate_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        timeout: float = 2.5,
        model: str | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del prompt, schema, timeout, model, session_id
        self.metrics.requests += 1
        if self._failures_before_success:
            self._failures_before_success -= 1
            self.metrics.failures += 1
            raise LLMUnavailableError("language model is unavailable") from None
        if not self._responses:
            self.metrics.failures += 1
            raise LLMUnavailableError("no language-model fixture is configured") from None
        self.metrics.successes += 1
        return copy.deepcopy(self._responses.pop(0))
