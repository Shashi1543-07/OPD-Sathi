"""Run all deterministic, synthetic evaluation gates without external providers."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    ("eval/interview/evaluate.py",),
    ("services/api/scripts/validate_redflag_rules.py",),
    ("eval/routing/evaluate.py", "--no-write"),
    ("eval/redflags/evaluate.py", "--no-write"),
    ("eval/extraction/evaluate.py",),
    ("eval/adversarial/evaluate.py",),
    ("eval/redflags_llm/evaluate.py",),
)


def main() -> None:
    """Stop at the first failed synthetic evaluation command."""
    for command in COMMANDS:
        subprocess.run([sys.executable, *command], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
