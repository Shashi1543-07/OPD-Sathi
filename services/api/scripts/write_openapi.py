"""Export the FastAPI OpenAPI schema for generated frontend types."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "services" / "api"))

from app.main import create_app  # noqa: E402

target = root / "services" / "api" / "openapi.json"
target.write_text(json.dumps(create_app().openapi(), indent=2) + "\n", encoding="utf-8")
