"""Shared contracts and privacy-safe aggregate metrics for language-model adapters."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMUnavailableError(RuntimeError):
    """A model call cannot safely produce a validated JSON result."""


class LLMProvider(Protocol):
    """Generate a JSON object under a caller-provided schema."""

    async def generate_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        timeout: float = 2.5,
        model: str | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class LLMMetrics:
    """Bounded aggregate counters; never retain prompts, outputs, or session ids."""

    requests: int = 0
    successes: int = 0
    failures: int = 0
    retries: int = 0
    circuit_open: int = 0
    call_limit_rejections: int = 0
    prompt_tokens: int = 0
    response_tokens: int = 0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=512))

    def safe_snapshot(self) -> dict[str, int | float | None]:
        """Return bounded aggregate counters and latency quantiles without request data."""
        ordered = sorted(self.latencies_ms)
        if ordered:
            p50 = ordered[(len(ordered) - 1) // 2]
            p95 = ordered[min(len(ordered) - 1, (95 * len(ordered) + 99) // 100 - 1)]
        else:
            p50 = None
            p95 = None
        return {
            "requests": self.requests,
            "successes": self.successes,
            "failures": self.failures,
            "retries": self.retries,
            "circuit_open": self.circuit_open,
            "call_limit_rejections": self.call_limit_rejections,
            "prompt_tokens": self.prompt_tokens,
            "response_tokens": self.response_tokens,
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
        }


class SessionCallBudget(Protocol):
    """Atomically account for model calls without retaining patient identifiers."""

    async def consume(self, session_id: str, limit: int) -> bool: ...
