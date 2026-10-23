"""Gunicorn settings for the API container.

Workers come from ``WEB_CONCURRENCY`` (see ``docs/SCALING.md`` for sizing: each worker opens up
to ``DB_POOL_MAX`` database connections). Workers are recycled after a bounded, jittered number
of requests so slow leaks cannot grow for ever, and never all at once. Live boards and status
pages reconnect by themselves when their worker restarts.
"""

import os

bind = "0.0.0.0:8000"
worker_class = "uvicorn_worker.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
# A worker that stops answering the arbiter for this long is replaced. Long-lived streams do
# not count against it: the async worker keeps reporting while they are open.
timeout = 60
# On restart or upgrade, requests in flight get this long to finish.
graceful_timeout = 30
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
# Structured application logs already record every request; no separate access log.
accesslog = None
