"""Export or verify the checked-in strict case-sheet JSON Schema."""

import argparse
import json
from pathlib import Path

from app.domain.case_sheet import CaseSheet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail instead of writing stale output")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[3] / "docs" / "case_sheet.schema.json"
    rendered = json.dumps(CaseSheet.model_json_schema(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not destination.exists() or destination.read_text(encoding="utf-8") != rendered:
            raise SystemExit("docs/case_sheet.schema.json is stale; run the schema exporter")
        return
    destination.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
