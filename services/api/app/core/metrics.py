"""Prometheus metrics for the API and the worker (Phase 7, task 7.9.1; ADR 010).

The API runs several Gunicorn processes. With ``PROMETHEUS_MULTIPROC_DIR`` set (the Compose
files set it to a tmpfs), every process writes its samples there and ``/metrics`` adds them
up, so a scrape sees the whole API rather than whichever process answered. The worker is one
process and serves its own metrics on ``WORKER_METRICS_PORT`` (``app.workers.metrics``).

Labels are bounded and never carry patient data: route templates (never raw paths),
department codes, job and provider names, outcomes.
"""

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)

# Request duration buckets around the 1.5 s p95 target (task 7.8.2).
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.5, 5.0, 10.0)

HTTP_REQUESTS = Counter(
    "http_requests_total", "Completed HTTP requests.", ("method", "route", "status")
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route", "status"),
    buckets=_LATENCY_BUCKETS,
)
AI_CALLS = Counter(
    "ai_requests_total", "Calls to AI and speech providers.", ("provider", "outcome")
)
AI_DURATION = Histogram(
    "ai_request_duration_seconds",
    "AI and speech provider call duration in seconds.",
    ("provider",),
    buckets=_LATENCY_BUCKETS,
)


def render() -> tuple[bytes, str]:
    """The metrics page: all API processes in multiprocess mode, else this process."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
