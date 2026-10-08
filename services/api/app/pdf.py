"""Render a compact, multilingual, history-only case sheet PDF."""

from html import escape
from typing import Any, cast

_LABELS = {
    "en": {
        "title": "Patient history",
        "queueNumber": "Queue number",
        "priority": "Attention reasons",
        "complaint": "Chief complaint",
        "present": "History of present complaint",
        "onset": "Onset",
        "location": "Location",
        "character": "Character",
        "aggravating": "Aggravating",
        "relieving": "Relieving",
        "radiation": "Radiation",
        "timing": "Pattern and timing",
        "duration": "Duration",
        "severity": "Severity (0–10)",
        "associated": "Associated symptoms",
        "negative": "Relevant negatives",
        "past": "Past history",
        "conditions": "Conditions",
        "surgeries": "Surgeries",
        "medications": "Medications stated",
        "allergies": "Allergies stated",
        "routing": "Department",
        "family": "Family history",
        "social": "Social history",
        "tobacco": "Tobacco",
        "alcohol": "Alcohol",
        "womenHealth": "Women's health",
        "pregnancy": "Pregnancy reported",
        "lmp": "Last menstrual period",
    },
    "hi": {
        "title": "रोगी का इतिहास",
        "queueNumber": "कतार क्रमांक",
        "priority": "ध्यान देने के कारण",
        "complaint": "मुख्य शिकायत",
        "present": "वर्तमान शिकायत का इतिहास",
        "onset": "शुरुआत",
        "location": "स्थान",
        "character": "स्वरूप",
        "aggravating": "बढ़ाने वाले कारण",
        "relieving": "राहत देने वाले कारण",
        "radiation": "दर्द का फैलाव",
        "timing": "समय और क्रम",
        "duration": "अवधि",
        "severity": "तीव्रता (0–10)",
        "associated": "साथ के लक्षण",
        "negative": "नकारात्मक जानकारी",
        "past": "पिछला इतिहास",
        "conditions": "स्थितियाँ",
        "surgeries": "शल्यक्रियाएँ",
        "medications": "बताई गई दवाएँ",
        "allergies": "बताई गई एलर्जी",
        "routing": "विभाग",
        "family": "पारिवारिक इतिहास",
        "social": "सामाजिक इतिहास",
        "tobacco": "तंबाकू",
        "alcohol": "शराब",
        "womenHealth": "महिलाओं का स्वास्थ्य",
        "pregnancy": "गर्भावस्था की जानकारी",
        "lmp": "अंतिम माहवारी",
    },
    "mr": {
        "title": "रुग्णाचा इतिहास",
        "queueNumber": "रांग क्रमांक",
        "priority": "लक्ष देण्याची कारणे",
        "complaint": "मुख्य तक्रार",
        "present": "सध्याच्या तक्रारीचा इतिहास",
        "onset": "सुरुवात",
        "location": "ठिकाण",
        "character": "स्वरूप",
        "aggravating": "त्रास वाढवणारे घटक",
        "relieving": "आराम देणारे घटक",
        "radiation": "वेदना पसरण्याचे ठिकाण",
        "timing": "वेळ आणि क्रम",
        "duration": "कालावधी",
        "severity": "तीव्रता (०–१०)",
        "associated": "सोबतची लक्षणे",
        "negative": "नकारात्मक माहिती",
        "past": "मागील इतिहास",
        "conditions": "आजार",
        "surgeries": "शस्त्रक्रिया",
        "medications": "सांगितलेली औषधे",
        "allergies": "सांगितलेल्या ॲलर्जी",
        "routing": "विभाग",
        "family": "कौटुंबिक इतिहास",
        "social": "सामाजिक इतिहास",
        "tobacco": "तंबाखू",
        "alcohol": "मद्य",
        "womenHealth": "महिलांचे आरोग्य",
        "pregnancy": "गर्भधारणेची माहिती",
        "lmp": "शेवटची मासिक पाळी",
    },
}
_DISCLAIMER = (
    "Collected by OPD Saathi from the patient's own statements. History only. Not a diagnosis."
)
_DURATION_UNITS = {
    "hi": {
        "minute": "मिनट",
        "hour": "घंटे",
        "day": "दिन",
        "week": "सप्ताह",
        "month": "महीने",
        "year": "वर्ष",
    },
    "mr": {
        "minute": "मिनिटे",
        "hour": "तास",
        "day": "दिवस",
        "week": "आठवडे",
        "month": "महिने",
        "year": "वर्षे",
    },
}


def _display(value: Any, language: str) -> str:
    if isinstance(value, list):
        return _list(value, language)
    if isinstance(value, dict):
        if "value" in value and "unit" in value:
            unit = _DURATION_UNITS.get(language, {}).get(str(value["unit"]), str(value["unit"]))
            return f"{value['value']} {unit}"
        if "name" in value:
            details = [str(value["name"])]
            if value.get("dose"):
                details.append(str(value["dose"]))
            return " · ".join(details)
        original = value.get("text_original")
        english = value.get("text_en")
        # Doctor-facing: English first, the patient's own words alongside when different.
        if english and original and english != original:
            return f"{english} ({original})"
        selected = english or original
        return str(selected) if selected else ""
    if value is None:
        return ""
    return str(value)


def _list(values: Any, language: str) -> str:
    if not isinstance(values, list):
        return ""
    return ", ".join(text for item in values if (text := _display(item, language)))


def _row(label: str, value: Any, language: str) -> str:
    rendered = _display(value, language)
    if not rendered:
        return ""
    return f"<div class='row'><dt>{escape(label)}</dt><dd>{escape(rendered)}</dd></div>"


def render_case_sheet_pdf(
    case_sheet: dict[str, Any],
    *,
    queue_number: str,
    attention_reasons: list[str],
    patient_line: str = "",
) -> bytes:
    """Return an A4 HTML-rendered PDF with escaped patient-provided text.

    The sheet is for the doctor, so labels and values are English; the patient's own
    words follow each translated value in brackets.
    """
    from weasyprint import HTML

    language = case_sheet.get("language", "en")
    labels = _LABELS["en"]
    hpi = case_sheet.get("hpi") or {}
    past = case_sheet.get("past_history") or {}
    complaint = _row(labels["complaint"], case_sheet.get("chief_complaint"), language)
    hpi_rows = "".join(
        (
            _row(labels["onset"], hpi.get("onset"), language),
            _row(labels["duration"], hpi.get("duration"), language),
            _row(labels["location"], hpi.get("location"), language),
            _row(labels["character"], hpi.get("character"), language),
            _row(labels["severity"], hpi.get("severity_0_10"), language),
            _row(labels["associated"], hpi.get("associated_symptoms"), language),
            _row(labels["negative"], hpi.get("pertinent_negatives"), language),
            _row(labels["aggravating"], hpi.get("aggravating"), language),
            _row(labels["relieving"], hpi.get("relieving"), language),
            _row(labels["radiation"], hpi.get("radiation"), language),
            _row(labels["timing"], hpi.get("timing"), language),
        )
    )
    past_rows = "".join(
        (
            _row(labels["conditions"], past.get("conditions"), language),
            _row(labels["surgeries"], past.get("surgeries"), language),
            _row(labels["medications"], case_sheet.get("medications"), language),
            _row(labels["allergies"], case_sheet.get("allergies"), language),
        )
    )
    social = case_sheet.get("social") or {}
    social_rows = "".join(
        (
            _row(labels["tobacco"], social.get("tobacco"), language),
            _row(labels["alcohol"], social.get("alcohol"), language),
        )
    )
    women = case_sheet.get("women_health") or {}
    women_rows = "".join(
        (
            _row(labels["pregnancy"], women.get("pregnant"), language),
            _row(labels["lmp"], women.get("lmp"), language),
        )
    )
    family_rows = _row(labels["family"], case_sheet.get("family_history"), language)
    social_block = (
        f"<h2>{escape(labels['social'])}</h2><dl>{social_rows}</dl>" if social_rows else ""
    )
    women_block = (
        f"<h2>{escape(labels['womenHealth'])}</h2><dl>{women_rows}</dl>" if women_rows else ""
    )
    ai_summary = case_sheet.get("ai_summary") or {}
    brief_block = (
        f"<div class='brief'><strong>AI summary (verify with patient):</strong> "
        f"{escape(str(ai_summary.get('brief_en')))}</div>"
        if ai_summary.get("brief_en")
        else ""
    )
    language_names = {"en": "English", "hi": "Hindi", "mr": "Marathi"}
    meta_line = " · ".join(
        part
        for part in (
            patient_line,
            f"Answered in {language_names.get(str(language), str(language))}",
        )
        if part
    )
    reasons = "".join(f"<li>{escape(reason)}</li>" for reason in attention_reasons if reason)
    routing = case_sheet.get("routing") or {}
    urgency_block = ""
    if reasons:
        urgency_block = (
            f"<div class='alert'><strong>{escape(labels['priority'])}:</strong>"
            f"<ul>{reasons}</ul></div>"
        )
    html = f"""<!doctype html>
<html lang="{escape(language)}">
<head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 12mm 13mm 15mm;
  @bottom-center {{ content: "{_DISCLAIMER}"; font: 7pt Inter, sans-serif;
    color: #555; }} }}
body {{ font: 9pt Inter, 'Noto Sans Devanagari', sans-serif;
  color: #17212b; line-height: 1.28; }}
h1 {{ font-size: 17pt; margin: 0 0 5pt; }}
h2 {{ font-size: 10pt; margin: 9pt 0 3pt; padding-bottom: 2pt;
  border-bottom: .5pt solid #9aa7b2; }}
.top {{ display:flex; justify-content:space-between;
  border-bottom: 1.5pt solid #25364a; padding-bottom: 5pt; }}
.token {{ font-size: 12pt; font-weight: 700; }}
.alert {{ margin-top: 5pt; color:#8a2500; }}
dl {{ margin:0; }}
.row {{ display:grid; grid-template-columns: 30% 70%; gap:5pt;
  padding:1.5pt 0; break-inside:avoid; }}
dt {{ color:#526170; }}
dd {{ margin:0; white-space:pre-wrap; overflow-wrap:anywhere; }}
ul {{ margin:2pt 0 0; padding-left:14pt; }}
.columns {{ display:grid; grid-template-columns:1fr 1fr; gap:10pt; }}
.meta {{ color:#526170; margin:3pt 0 0; }}
.brief {{ margin-top:6pt; padding:5pt 7pt; background:#eef6ff; border-radius:3pt; }}
</style></head><body>
<div class="top">
  <div><h1>{escape(labels["title"])}</h1><p class="meta">{escape(meta_line)}</p></div>
  <div class="token">{escape(labels["queueNumber"])}: {escape(queue_number or "—")}</div>
</div>
{urgency_block}
{brief_block}
<h2>{escape(labels["complaint"])}</h2><dl>{complaint}</dl>
<div class="columns">
  <section><h2>{escape(labels["present"])}</h2><dl>{hpi_rows}</dl></section>
  <section><h2>{escape(labels["past"])}</h2><dl>{past_rows}</dl>
    {_row(labels["routing"], routing.get("department_code"), language)}
    {family_rows}
    {social_block}
    {women_block}
  </section>
</div>
</body></html>"""
    return cast(bytes, HTML(string=html).write_pdf())


def warm_pdf_renderer() -> bool:
    """Load fonts and Pango before readiness so the first export stays responsive.

    Returns False when the native WeasyPrint libraries are missing (for example on a
    Windows host without GTK); the API still starts and only PDF export is unavailable.
    """
    try:
        _warm()
    except OSError:
        return False
    return True


def _warm() -> None:
    render_case_sheet_pdf(
        {
            "language": "en",
            "chief_complaint": {"text_original": "", "text_en": ""},
            "hpi": {},
            "past_history": {},
            "medications": [],
            "allergies": [],
            "red_flags": [],
            "routing": {},
        },
        queue_number="DEMO",
        attention_reasons=[],
    )
