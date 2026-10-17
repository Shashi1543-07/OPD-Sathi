"""Write the fictional demo papers to docs/demo-papers (see app/demo_papers.py).

    python services/api/scripts/make_demo_papers.py
"""

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "services" / "api"))

from app.demo_papers import lab_report_png, prescription_png  # noqa: E402

target = root / "docs" / "demo-papers"
target.mkdir(parents=True, exist_ok=True)
for name, draw in (
    ("synthetic-lab-report.png", lab_report_png),
    ("synthetic-prescription.png", prescription_png),
):
    path = target / name
    path.write_bytes(draw())
    print(f"wrote {path} ({path.stat().st_size // 1024} kB)")
