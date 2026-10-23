"""Print-ready paper fallback pack, from the same content as the kiosk (Phase 7, task 7.7.7).

When the server is down the OPD checks patients in on paper (docs/runbooks/server-down.md).
The forms ask the same danger-sign and core questions as the kiosk, in English, Hindi and
Marathi, taken from the question bank and the kiosk's own translations, so paper and screen
never drift apart. Writes docs/paper/: an intake form per language, a token roll and a kiosk
sign, as A4 HTML pages to print from any browser.

Usage: python scripts/make_paper_forms.py [--check]   (--check fails when docs/paper is stale)
"""

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "services" / "api" / "app" / "domain" / "interview" / "bank"
LOCALES = ROOT / "apps" / "web" / "src" / "locales"
OUT = ROOT / "docs" / "paper"
LANGUAGES = ("en", "hi", "mr")

# Paper-only wording (draft: native-speaker review with all other text, task 7.6.2).
PAPER = {
    "en": {
        "title": "OPD intake form (paper)",
        "subtitle": (
            "Used only while the computer system is down. Staff copy into the system later."
        ),
        "paper_token": "Paper token",
        "department": "Department",
        "arrived": "Date and time of arrival",
        "danger_note": "Ask first. If YES to any: take the patient to the nurse now.",
        "yes": "Yes",
        "no": "No",
        "about": "About the patient",
        "main_problem": "Main problem (tick one or write it)",
        "other": "Other",
        "anything_else": "Anything else the doctor should know",
        "nurse": "Nurse use",
        "urgency": "Urgency",
        "routine": "Routine",
        "priority": "Priority",
        "urgent": "Urgent",
        "entered": "Entered in the system as token",
        "entered_by": "Entered by",
        "roll_title": "Paper token roll",
        "roll_note": "One roll per department. Give tokens in order; write the token on the form.",
        "sign_title": "Check-in is on paper today",
        "sign_body": "Please go to the OPD desk. If you feel very unwell, tell the staff now.",
    },
    "hi": {
        "title": "ओपीडी पंजीकरण फ़ॉर्म (कागज़)",
        "subtitle": "केवल तब, जब कंप्यूटर सिस्टम बंद हो। स्टाफ़ बाद में सिस्टम में दर्ज करेगा।",
        "paper_token": "कागज़ी टोकन",
        "department": "विभाग",
        "arrived": "आने की तारीख और समय",
        "danger_note": "पहले पूछें। किसी का भी उत्तर हाँ हो तो मरीज़ को तुरंत नर्स के पास ले जाएँ।",
        "yes": "हाँ",
        "no": "नहीं",
        "about": "मरीज़ के बारे में",
        "main_problem": "मुख्य परेशानी (एक पर निशान लगाएँ या लिखें)",
        "other": "अन्य",
        "anything_else": "डॉक्टर को और कुछ बताना हो तो",
        "nurse": "नर्स के लिए",
        "urgency": "तात्कालिकता",
        "routine": "सामान्य",
        "priority": "प्राथमिकता",
        "urgent": "तत्काल",
        "entered": "सिस्टम में दर्ज टोकन",
        "entered_by": "दर्ज करने वाले",
        "roll_title": "कागज़ी टोकन रोल",
        "roll_note": "हर विभाग का एक रोल। टोकन क्रम से दें; टोकन फ़ॉर्म पर लिखें।",
        "sign_title": "आज पंजीकरण कागज़ पर हो रहा है",
        "sign_body": "कृपया ओपीडी डेस्क पर जाएँ। अगर आपकी तबीयत बहुत ख़राब है, तो अभी स्टाफ़ को बताएँ।",
    },
    "mr": {
        "title": "ओपीडी नोंदणी फॉर्म (कागद)",
        "subtitle": "फक्त संगणक प्रणाली बंद असताना. कर्मचारी नंतर प्रणालीत नोंद करतील.",
        "paper_token": "कागदी टोकन",
        "department": "विभाग",
        "arrived": "येण्याची तारीख आणि वेळ",
        "danger_note": "आधी विचारा. कोणत्याही प्रश्नाचे उत्तर होय असल्यास रुग्णाला लगेच परिचारिकेकडे न्या.",
        "yes": "होय",
        "no": "नाही",
        "about": "रुग्णाविषयी",
        "main_problem": "मुख्य त्रास (एकावर खूण करा किंवा लिहा)",
        "other": "इतर",
        "anything_else": "डॉक्टरांना आणखी काही सांगायचे असल्यास",
        "nurse": "परिचारिकेसाठी",
        "urgency": "तातडी",
        "routine": "नेहमीचे",
        "priority": "प्राधान्य",
        "urgent": "तातडीचे",
        "entered": "प्रणालीत नोंदवलेला टोकन",
        "entered_by": "नोंद करणारे",
        "roll_title": "कागदी टोकन रोल",
        "roll_note": "प्रत्येक विभागासाठी एक रोल. टोकन क्रमाने द्या; टोकन फॉर्मवर लिहा.",
        "sign_title": "आज नोंदणी कागदावर होत आहे",
        "sign_body": "कृपया ओपीडी डेस्कवर जा. तुम्हाला खूप अस्वस्थ वाटत असल्यास आत्ताच कर्मचाऱ्यांना सांगा.",
    },
}

STYLE = """
@page { size: A4; margin: 12mm; }
* { box-sizing: border-box; }
body { font: 11pt/1.35 "Noto Sans", "Noto Sans Devanagari", "Nirmala UI", Mangal, sans-serif;
  color: #000; background: #fff; margin: 0; }
h1 { font-size: 16pt; margin: 0 0 2pt; } h2 { font-size: 11.5pt; margin: 6pt 0 2pt;
  border-bottom: 1.5pt solid #000; padding-bottom: 1pt; }
.note { font-size: 9pt; margin: 0 0 6pt; }
.head, .row { display: flex; gap: 10pt; flex-wrap: wrap; }
.field { flex: 1 1 30%; min-width: 45mm; border-bottom: 1pt solid #000; padding-top: 10pt;
  font-size: 9pt; }
.danger { border: 2pt solid #000; padding: 4pt 6pt; }
.danger strong { display: block; margin-bottom: 3pt; }
table { width: 100%; border-collapse: collapse; }
td { padding: 3pt 2pt; border-bottom: 0.5pt solid #666; vertical-align: top; }
td.box { width: 22mm; white-space: nowrap; }
.tick { display: inline-block; width: 10pt; height: 10pt; border: 1pt solid #000;
  margin-right: 4pt; vertical-align: -1pt; }
.options { columns: 2; column-gap: 12pt; }
.options > span { display: block; break-inside: avoid; padding: 1pt 0; }
.scale span { display: inline-block; width: 15pt; height: 15pt; border: 1pt solid #000;
  text-align: center; margin-right: 2pt; font-size: 9pt; line-height: 15pt; }
.lines { border-bottom: 1pt solid #000; height: 14pt; }
.nurse { border: 1pt dashed #000; padding: 4pt 6pt; margin-top: 8pt; }
.footer { font-size: 7.5pt; margin-top: 6pt; color: #333; }
.roll { display: grid; grid-template-columns: repeat(5, 1fr); }
.roll div { border: 1pt dashed #000; height: 18mm; display: flex; flex-direction: column;
  align-items: center; justify-content: center; font-size: 15pt; font-weight: 700; }
.roll small { font-size: 7pt; font-weight: 400; }
.sign { text-align: center; padding-top: 30mm; }
.sign h1 { font-size: 30pt; margin: 6mm 0 2mm; } .sign p { font-size: 16pt; margin: 0 0 14mm; }
"""


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def flat(language: str, namespace: str) -> dict[str, str]:
    out: dict[str, str] = {}

    def walk(value: object, prefix: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, str):
            out[prefix] = value

    walk(json.loads((LOCALES / language / f"{namespace}.json").read_text(encoding="utf-8")), "")
    return out


def label(language: str, key: str) -> str:
    """A kiosk string, so paper says exactly what the screen says."""
    for namespace in ("intake", "kiosk"):
        strings = flat(language, namespace)
        if key in strings:
            return strings[key]
    raise SystemExit(f"apps/web/src/locales/{language} has no {key} (intake.json, kiosk.json)")


def page(language: str, title: str, body: str) -> str:
    return (
        f'<!doctype html>\n<html lang="{language}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{esc(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n{body}\n"
        "</body>\n</html>\n"
    )


def intake_form(language: str, core: dict[str, dict], tiles: dict[str, dict]) -> str:
    words = PAPER[language]
    yes_no = (
        f'<span class="tick"></span>{esc(words["yes"])} &nbsp; '
        f'<span class="tick"></span>{esc(words["no"])}'
    )
    danger_rows = "".join(
        f'<tr><td>{esc(text[language])}</td><td class="box">{yes_no}</td></tr>'
        for text in tiles.values()
    )
    concern, duration, severity = core["chief_concern"], core["duration"], core["severity"]
    concern_options = "".join(
        f'<span><span class="tick"></span>{esc(option["label"][language])}</span>'
        for option in concern["options"]
    ) + f'<span><span class="tick"></span>{esc(words["other"])}: ____________</span>'
    duration_options = "".join(
        f'<span><span class="tick"></span>{esc(option["label"][language])}</span>'
        for option in duration["options"]
    )
    scale = "".join(f"<span>{number}</span>" for number in range(11))
    sexes = " &nbsp; ".join(
        f'<span class="tick"></span>{esc(label(language, key))}'
        for key in ("sexFemale", "sexMale", "sexOther")
    )
    body = f"""
<h1>{esc(words["title"])}</h1>
<p class="note">{esc(words["subtitle"])}</p>
<div class="head">
  <div class="field">{esc(words["paper_token"])}: P-______</div>
  <div class="field">{esc(words["department"])}</div>
  <div class="field">{esc(words["arrived"])}</div>
</div>
<h2>1 · {esc(label(language, "dangerTitle"))}</h2>
<div class="danger"><strong>{esc(words["danger_note"])}</strong>
<table>{danger_rows}</table></div>
<h2>2 · {esc(words["about"])}</h2>
<div class="row">
  <div class="field" style="flex-basis:100%">{esc(label(language, "nameQuestion"))}</div>
  <div class="field">{esc(label(language, "ageLabel"))}</div>
  <div class="field">{esc(label(language, "sexLabel"))}: {sexes}</div>
  <div class="field">{esc(label(language, "phoneLabel"))}</div>
  <div class="field">{esc(label(language, "opdCardNumber"))}</div>
</div>
<h2>3 · {esc(concern["prompt"][language])}</h2>
<p class="note">{esc(words["main_problem"])}</p>
<div class="options">{concern_options}</div>
<h2>4 · {esc(duration["prompt"][language])}</h2>
<div class="options">{duration_options}</div>
<h2>5 · {esc(severity["prompt"][language])}</h2>
<div class="scale">{scale}</div>
<h2>6 · {esc(words["anything_else"])}</h2>
<div class="lines"></div><div class="lines"></div>
<div class="nurse"><strong>{esc(words["nurse"])}</strong>
<div class="row">
  <div class="field">{esc(words["urgency"])}:
    <span class="tick"></span>{esc(words["routine"])}
    <span class="tick"></span>{esc(words["priority"])}
    <span class="tick"></span>{esc(words["urgent"])}</div>
  <div class="field">{esc(words["entered"])}</div>
  <div class="field">{esc(words["entered_by"])}</div>
</div></div>
<p class="footer">OPD Saathi · docs/paper · scripts/make_paper_forms.py · {esc(language)}</p>
"""
    return page(language, words["title"], body)


def token_roll() -> str:
    cells = "".join(f"<div>P-{number:03d}<small>OPD</small></div>" for number in range(1, 101))
    notes = "<br>".join(esc(PAPER[language]["roll_note"]) for language in LANGUAGES)
    titles = " · ".join(esc(PAPER[language]["roll_title"]) for language in LANGUAGES)
    return page("en", PAPER["en"]["roll_title"], f'<h1>{titles}</h1><p class="note">{notes}</p>'
                f'<div class="roll">{cells}</div>')


def kiosk_sign() -> str:
    body = '<div class="sign">' + "".join(
        f'<h1 lang="{language}">{esc(PAPER[language]["sign_title"])}</h1>'
        f'<p lang="{language}">{esc(PAPER[language]["sign_body"])}</p>'
        for language in LANGUAGES
    ) + "</div>"
    return page("en", PAPER["en"]["sign_title"], body)


def build() -> dict[str, str]:
    core = {
        question["id"]: question
        for question in json.loads((BANK / "core.json").read_text(encoding="utf-8"))["questions"]
    }
    tiles = json.loads((BANK / "danger_tiles.json").read_text(encoding="utf-8"))["tiles"]
    files = {f"intake-form-{language}.html": intake_form(language, core, tiles)
             for language in LANGUAGES}
    files["token-roll.html"] = token_roll()
    files["kiosk-sign.html"] = kiosk_sign()
    return files


def main() -> int:
    files = build()
    if "--check" in sys.argv:
        stale = [name for name, text in files.items()
                 if not (OUT / name).is_file()
                 or (OUT / name).read_text(encoding="utf-8") != text]
        if stale:
            print("docs/paper is out of date; run python scripts/make_paper_forms.py:", *stale)
            return 1
        print("docs/paper matches the question bank and translations.")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {len(files)} files to docs/paper.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
