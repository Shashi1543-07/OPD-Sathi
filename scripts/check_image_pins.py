"""Fail when a container image is not pinned by digest, or the web image's Node drifts from CI.

Phase 7, task 7.0.4: every base and service image is pinned by ``@sha256:`` so a rebuild gets
exactly the bytes that were tested, and the web image is built with the same Node release that
CI tests with (``apps/web/.nvmrc``). Dependabot proposes digest updates weekly.

Usage: python scripts/check_image_pins.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "services/api/Dockerfile",
    "apps/web/Dockerfile",
    "docker-compose*.yml",
    "infra/**/*.yml",
    ".github/workflows/*.yml",
)
# FROM <image>, image: <image>, or `docker run ... <image>` in a workflow.
IMAGE = re.compile(
    r"^\s*(?:FROM\s+(?:--platform=\S+\s+)?|image:\s*)(?P<image>[^\s#\"']+)"
    r"|docker run [^\n]*?(?P<run>[a-z0-9./_-]+/[a-z0-9._-]+:[\w.-]+(?:@sha256:[0-9a-f]{64})?)",
    re.MULTILINE,
)


def main() -> int:
    problems = []
    for pattern in SOURCES:
        for path in sorted(ROOT.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            stages = set(re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", text, flags=re.M | re.I))
            for match in IMAGE.finditer(text):
                image = match.group("image") or match.group("run")
                # "image: !reset null" (Compose) removes an inherited image: nothing to pin.
                if not image or image in stages or "${" in image or image in {"scratch", "!reset"}:
                    continue
                if not re.search(r"@sha256:[0-9a-f]{64}$", image):
                    line = text.count("\n", 0, match.start()) + 1
                    problems.append(f"{path.relative_to(ROOT)}:{line}: {image} has no digest")
    nvmrc = (ROOT / "apps/web/.nvmrc").read_text(encoding="utf-8").strip()
    dockerfile = (ROOT / "apps/web/Dockerfile").read_text(encoding="utf-8")
    node = re.search(r"^FROM\s+node:(\d+\.\d+\.\d+)", dockerfile, flags=re.M)
    if not node or node.group(1) != nvmrc:
        found = node.group(1) if node else "none"
        problems.append(f"apps/web/Dockerfile builds with Node {found}; CI tests Node {nvmrc}")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"Every image is pinned by digest; web image and CI use Node {nvmrc}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
