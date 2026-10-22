"""Run existing deterministic dependency-failure tests as a safe chaos smoke suite."""

from __future__ import annotations

import subprocess
import sys

TESTS = (
    "services/api/tests/unit/test_ratelimit.py",
    "services/api/tests/unit/test_queue_api.py",
    "services/api/tests/unit/test_queue_worker.py",
    "services/api/tests/unit/test_llm_provider.py",
    "services/api/tests/unit/test_stt_providers.py",
)

raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", *TESTS, "-q"]))
