"""Destructive dependency recovery drill guarded to the Phase 13 stack only."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

PROJECT = "opd-saathi-phase13"
API_URL = "http://127.0.0.1:18000"
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    PROJECT,
    "--env-file",
    ".env.phase13",
    "-f",
    "docker-compose.yml",
    "-f",
    "infra/chaos/docker-compose.isolated.yml",
]
SERVICES = ("postgres", "redis", "api", "worker")


def compose(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        [*COMPOSE, *arguments], capture_output=True, text=True, check=False
    )
    if check and result.returncode:
        raise RuntimeError(f"docker compose {arguments[0]} failed: {result.stderr[-500:]}")
    return result.stdout.strip()


def assert_isolated() -> None:
    for service in SERVICES:
        container_id = compose("ps", "-q", service)
        if not container_id:
            raise RuntimeError(f"isolated service is missing: {service}")
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{ index .Config.Labels "com.docker.compose.project" }}|'
                '{{ index .Config.Labels "opd-saathi.phase13.isolated" }}',
                container_id,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip() != f"{PROJECT}|true":
            raise RuntimeError(f"refusing destructive drill: {service} is not isolated")


def request(path: str) -> tuple[int, str]:
    try:
        with urlopen(f"{API_URL}{path}", timeout=2) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8")
    except (URLError, TimeoutError, OSError):
        return 0, "unavailable"


def wait_for(path: str, status: int, timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    last_body = ""
    while time.monotonic() < deadline:
        actual, last_body = request(path)
        if actual == status:
            return last_body
        time.sleep(0.5)
    raise RuntimeError(f"{path} did not return {status}; last status/body: {last_body[:180]}")


def run_drill(name: str, actions: Sequence[tuple[str, ...]], expected: tuple[str, int]) -> None:
    print(f"START {name}")
    for action in actions:
        compose(*action)
    body = wait_for(expected[0], expected[1])
    print(f"PASS {name}: {expected[0]} returned {expected[1]}")
    if expected[1] == 503:
        details = json.loads(body)
        if details.get("code") != "DEPENDENCY_UNAVAILABLE":
            raise RuntimeError(f"{name}: readiness failed with an unexpected problem code")


def wait_for_service_running(service: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        running = compose("ps", "--status", "running", "--services").splitlines()
        if service in running:
            return
        time.sleep(0.5)
    raise RuntimeError(f"isolated service did not recover: {service}")


def main() -> int:
    if "--confirm-isolated" not in sys.argv:
        print("Refusing destructive actions without --confirm-isolated.", file=sys.stderr)
        return 2
    assert_isolated()
    wait_for("/readyz", 200)
    try:
        run_drill(
            "Redis stop and recovery",
            (("stop", "redis"),),
            ("/readyz", 503),
        )
    finally:
        compose("start", "redis")
    wait_for("/readyz", 200)
    print("PASS Redis recovery: readiness returned 200")

    try:
        compose("stop", "worker")
        if request("/healthz")[0] != 200 or request("/readyz")[0] != 200:
            raise RuntimeError("worker outage unexpectedly took down the API")
    finally:
        compose("start", "worker")
    wait_for_service_running("worker")
    print("PASS worker stop and recovery: API remained ready; worker restarted")

    try:
        compose("stop", "postgres")
        wait_for("/readyz", 503)
        print("PASS PostgreSQL stop: readiness returned 503")
    finally:
        compose("start", "postgres")
    wait_for("/readyz", 200)
    print("PASS PostgreSQL recovery: readiness returned 200")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
