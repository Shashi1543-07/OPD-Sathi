"""Saved, non-clinical response fixtures for network-independent demonstrations."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.ai.providers.llm.base import LLMMetrics

_FIXTURES_PATH = Path(__file__).with_name("demo_safe.json")


class DemoSafeLLM:
    """Replay schema-bounded saved fixtures without constructing a network client."""

    name = "demo-safe"

    def __init__(self) -> None:
        self._fixtures: dict[str, Any] = json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))
        self.metrics = LLMMetrics()

    async def generate_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        timeout: float = 2.5,
        model: str | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del prompt, timeout, model, session_id
        self.metrics.requests += 1
        properties = schema.get("properties", {})
        result: dict[str, Any] = {}
        if isinstance(properties, Mapping):
            for name, definition in properties.items():
                if name not in self._fixtures or not isinstance(definition, Mapping):
                    continue
                fixture = self._fixtures[name]
                if isinstance(fixture, Mapping) and fixture.get("enum") == "first":
                    choices = definition.get("enum", [])
                    if choices:
                        result[name] = choices[0]
                else:
                    result[name] = fixture
        self.metrics.successes += 1
        return result
