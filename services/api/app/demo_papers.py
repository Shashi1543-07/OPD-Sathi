"""Fictional old papers for demonstrations, drawn as PNGs: a test report and a prescription.

Every name, value and address on them is invented, and each paper says so in print. The demo's
returning patient brought the prescription to her earlier visit; the presenter photographs the
test report (or picks it as a file on a computer without a camera) on the kiosk's review
screen, and with AI help the doctor sees it read beside the photo.
"""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1100
INK = (28, 33, 40)
FAINT = (110, 118, 128)
RULE = (190, 196, 204)
MARK = (176, 30, 30)
Colour = tuple[int, int, int]


class _Sheet:
    def __init__(self, height: int) -> None:
        self.image = Image.new("RGB", (WIDTH, height), (252, 251, 247))
        self.draw = ImageDraw.Draw(self.image)
        self.y = 56

    def line(self, words: str, *, size: int = 26, fill: Colour = INK, gap: int = 14) -> None:
        self.draw.text((64, self.y), words, font=ImageFont.load_default(size=size), fill=fill)
        self.y += size + gap

    def note(self, words: str) -> None:
        self.line(words, size=22, fill=FAINT)

    def heading(self, words: str) -> None:
        self.line(words, size=24, fill=FAINT, gap=12)

    def rule(self, gap: int = 22) -> None:
        self.y += 6
        self.draw.line((64, self.y, WIDTH - 64, self.y), fill=RULE, width=2)
        self.y += gap

    def row(self, columns: tuple[int, ...], cells: tuple[str, ...], *, size: int = 25) -> None:
        font = ImageFont.load_default(size=size)
        for x, words in zip(columns, cells, strict=False):
            # The laboratory's own high and low marks are printed in red, as many laboratories do.
            fill = MARK if words in {"H", "L"} else INK
            self.draw.text((x, self.y), words, font=font, fill=fill)
        self.y += size + 16

    def png(self) -> bytes:
        output = BytesIO()
        self.image.save(output, format="PNG", optimize=True)
        return output.getvalue()


def lab_report_png() -> bytes:
    """A blood and urine report with printed reference ranges and H/L marks."""
    sheet = _Sheet(1000)
    sheet.line("SYNTHETIC DIAGNOSTICS LABORATORY", size=38, gap=10)
    sheet.note("12 Sample Road, Nagpur 440001  ·  Fictional laboratory for demonstrations")
    sheet.rule()
    sheet.line("Patient: DEMO PATIENT (fictional)          Age / Sex: 52 years / Female")
    sheet.line("Sample collected: 30-Sep-2026               Reported: 30-Sep-2026")
    sheet.line("Referred by: Dr. A. Demo (fictional)")
    sheet.rule()
    # Test, result, the laboratory's mark, unit, reference range.
    columns = (64, 450, 580, 640, 790)
    sheet.row((64, 450, 640, 790), ("Test", "Result", "Unit", "Reference range"))
    sheet.rule(gap=14)
    sections = {
        "COMPLETE BLOOD COUNT": (
            ("Haemoglobin", "9.8", "L", "g/dL", "12.0 - 15.0"),
            ("Total leucocyte count", "7,400", "", "/cumm", "4,000 - 11,000"),
            ("Platelet count", "2,10,000", "", "/cumm", "1,50,000 - 4,50,000"),
        ),
        "BIOCHEMISTRY": (
            ("Fasting blood glucose", "132", "H", "mg/dL", "70 - 100"),
            ("HbA1c", "7.4", "H", "%", "< 5.7"),
            ("Serum creatinine", "0.9", "", "mg/dL", "0.6 - 1.1"),
        ),
        "MICROBIOLOGY": (("Urine culture", "No growth"),),
    }
    for index, (section, results) in enumerate(sections.items()):
        if index:
            sheet.y += 10
        sheet.heading(section)
        for result in results:
            sheet.row(columns, result)
    sheet.rule()
    sheet.note("H = above, L = below the reference range of this laboratory.")
    sheet.y += 30
    sheet.line("SYNTHETIC RECORD FOR SOFTWARE DEMONSTRATION.", fill=MARK, gap=8)
    sheet.line("NOT A REAL PATIENT, LABORATORY OR RESULT.", fill=MARK)
    return sheet.png()


def prescription_png() -> bytes:
    """A handwritten-style outpatient prescription, printed for legibility."""
    sheet = _Sheet(800)
    sheet.line("Dr. A. Demo, MBBS, MD (fictional)", size=36, gap=8)
    sheet.note("Synthetic Clinic, 4 Example Lane, Nagpur  ·  Fictional clinic for demonstrations")
    sheet.rule()
    sheet.line("Date: 02-Sep-2026")
    sheet.line("Patient: Demo patient (fictional), 52 years, Female")
    sheet.rule()
    sheet.line("Diagnosis: Type 2 diabetes mellitus; Hypertension")
    sheet.y += 14
    sheet.line("Rx", size=40, gap=18)
    # Medicine, how often, when, for how long.
    columns = (64, 520, 640, 830)
    for medicine in (
        ("1.  Tab Metformin 500 mg", "1-0-1", "after food", "x 30 days"),
        ("2.  Tab Amlodipine 5 mg", "0-0-1", "", "x 30 days"),
        ("3.  Tab Atorvastatin 10 mg", "0-0-1", "", "x 30 days"),
    ):
        sheet.row(columns, medicine, size=27)
    sheet.y += 18
    sheet.line("Review after 1 month with a fasting blood sugar report.")
    sheet.y += 50
    sheet.line("SYNTHETIC RECORD FOR SOFTWARE DEMONSTRATION.", fill=MARK, gap=8)
    sheet.line("NOT A REAL PATIENT, DOCTOR OR PRESCRIPTION.", fill=MARK)
    return sheet.png()
