"""Fail when .env.example documents a setting that nothing reads (Phase 7, task 7.0.3).

An operator who changes such a setting changes nothing, which is worse than not offering it.
A key counts as read when it is an API ``Settings`` field, or when a file that consumes the
environment names it: Compose files (interpolation), the setup and backup scripts, the
Makefile, the API's scripts, the evaluation runners, or the web build (``import.meta.env``).
Documentation does not count.

Usage: python scripts/check_env_example.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.config import Settings  # noqa: E402

CONSUMERS = (
    "docker-compose*.yml",
    "infra/**/*.yml",
    "infra/**/*.conf",
    "*.ps1",
    "scripts/*.sh",
    "scripts/*.ps1",
    "scripts/*.py",
    "Makefile",
    "services/api/scripts/*.py",
    "services/api/scripts/*.sh",
    "services/api/app/cli.py",
    "services/api/gunicorn.conf.py",
    "eval/**/*.py",
    "apps/web/vite.config.ts",
    "apps/web/src/**/*.ts",
    "apps/web/src/**/*.tsx",
)
SELF = Path(__file__).resolve()


def documented_keys() -> list[str]:
    keys = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            keys.append(match.group(1))
    return keys


def consumer_text() -> str:
    seen: set[Path] = set()
    parts = []
    for pattern in CONSUMERS:
        for path in sorted(ROOT.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen or resolved == SELF or "node_modules" in path.parts:
                continue
            seen.add(resolved)
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def main() -> int:
    settings_keys = {name.upper() for name in Settings.model_fields}
    text = consumer_text()
    unread = [
        key
        for key in documented_keys()
        if key not in settings_keys and not re.search(rf"\b{re.escape(key)}\b", text)
    ]
    if unread:
        print("These .env.example settings are read by nothing; wire them or remove them:")
        for key in unread:
            print(f"  {key}")
        return 1
    print(f"All {len(documented_keys())} .env.example settings are read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
