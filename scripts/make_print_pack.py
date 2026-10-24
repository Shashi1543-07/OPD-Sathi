"""Staff cards and patient signs to print (Phase 7, tasks 7.15.2 and 7.15.3).

One-page cards for nurses, doctors and administrators, and two signs for the waiting area
("How to check in" and "If you feel very unwell, tell the staff now"), in English, Hindi and
Marathi, as A4 HTML pages to print from any browser. Every button or screen name on them is
taken from the app's own translations ([[namespace:key]] below), so a card always says what
the screen says. The privacy-notice poster carries the hospital's own address and QR, so it
is printed from Settings instead.

Draft wording: reviewed with the native-speaker review (task 7.6.2) and used in training
(task 7.15.4).

Usage: python scripts/make_print_pack.py [--check]   (--check fails when docs/print is stale)
"""

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "apps" / "web" / "src" / "locales"
OUT = ROOT / "docs" / "print"
LANGUAGES = ("en", "hi", "mr")
LABEL = re.compile(r"\[\[(staff|kiosk|intake|common):([A-Za-z0-9_]+)\]\]")

STYLE = """
@page { size: A4; margin: 13mm; }
* { box-sizing: border-box; }
body { font-family: "Nirmala UI", "Noto Sans Devanagari", "Segoe UI", Arial, sans-serif;
  color: #000; font-size: 10.5pt; line-height: 1.38; margin: 0; }
h1 { font-size: 19pt; margin: 0 0 1mm; }
.lead { margin: 0 0 4mm; font-size: 10pt; color: #222; }
ol.card { list-style: none; counter-reset: step; margin: 0; padding: 0; }
ol.card > li { counter-increment: step; border: 1pt solid #000; border-radius: 3mm;
  padding: 2.6mm 3.4mm 2.6mm 12mm; margin: 0 0 2.6mm; position: relative; break-inside: avoid; }
ol.card > li::before { content: counter(step); position: absolute; left: 3.2mm; top: 2.4mm;
  font-weight: 700; font-size: 13pt; }
ol.card h2 { font-size: 11pt; margin: 0 0 0.6mm; }
ol.card p { margin: 0; }
b.ui { border: 0.8pt solid #000; border-radius: 1.4mm; padding: 0 1.2mm; white-space: nowrap; }
.footer { font-size: 7.5pt; margin-top: 3mm; color: #333; }
.sign { text-align: center; }
.sign section { border-bottom: 1.2pt dashed #000; padding: 6mm 0 7mm; }
.sign section:last-of-type { border-bottom: 0; }
.sign h1 { font-size: 24pt; }
.sign ol { display: inline-block; text-align: left; font-size: 13.5pt; margin: 3mm 0 0;
  padding-left: 8mm; }
.sign ol li { margin: 1.2mm 0; }
.sign .urgent { font-size: 13.5pt; font-weight: 700; margin: 3mm 0 0; }
.unwell section { padding: 16mm 0; }
.unwell h1 { font-size: 34pt; line-height: 1.25; margin: 0; }
"""

# Each card: a title, one line on what OPD Saathi is, and its steps as (heading, text).
CARDS: dict[str, dict[str, dict[str, object]]] = {
    "nurse": {
        "en": {
            "title": "OPD Saathi · nurse's card",
            "lead": "OPD Saathi takes the patient's history at the kiosk and keeps the queue. "
            "It never diagnoses. Patients come first; the screen comes second.",
            "steps": [
                ("Sign in", "Open https://<server>/staff on the desk computer with your own "
                 "email and password. The first time, choose your own password. Never share "
                 "an account."),
                ("Urgent alerts come first", "A red alert at the top of [[staff:queue]] means "
                 "a patient tapped a danger sign or asked for help. Go to the patient now, then "
                 "press [[staff:qbAcknowledge]]. Keep [[staff:qbSound]] on."),
                ("Check what the kiosk cannot", "[[staff:nurseCheckShort]]: confirm the danger "
                 "sign in person. To change a patient's place, open ⋯ → "
                 "[[staff:qbChangePriority]], give a reason, then [[staff:qbApplyOverride]]. If "
                 "the department is not confirmed, type a reason and press "
                 "[[staff:qbConfirmDepartment]]."),
                ("Calling patients", "[[staff:callNext]] calls the next patient in calling "
                 "order, urgent first. If they do not come, wait for the countdown, then "
                 "[[staff:markAbsent]]. They move to [[staff:missedTitle]]; [[staff:recall]] "
                 "puts them back in their place with the same token."),
                ("Patients who cannot use the kiosk", "[[staff:patients]] → "
                 "[[staff:assistedRegistration]]: read the consent notice with the patient "
                 "first, then enter their answers with them."),
                ("Lost OPD card", "[[staff:patients]] → [[staff:reissueTitle]]: find the "
                 "patient, check their age and sex with them, then [[staff:reissueAction]]. "
                 "Give them the new PIN; the old one stops working."),
                ("If the system is down", "Use the paper pack at the desk: the paper intake "
                 "form and the paper token roll (P-001 …). Enter those visits once the system "
                 "is back."),
                ("Help", "IT contact: ______________________ Phone: ______________"),
            ],
        },
        "hi": {
            "title": "OPD Saathi · नर्स के लिए कार्ड",
            "lead": "OPD Saathi कियोस्क पर मरीज़ का इतिहास लेता है और कतार चलाता है। यह निदान "
            "कभी नहीं करता। पहले मरीज़, फिर स्क्रीन।",
            "steps": [
                ("साइन इन", "डेस्क के कंप्यूटर पर https://<server>/staff खोलें और अपने ईमेल और "
                 "पासवर्ड से साइन इन करें। पहली बार अपना पासवर्ड खुद चुनें। अपना खाता कभी किसी से "
                 "साझा न करें।"),
                ("तत्काल अलर्ट सबसे पहले", "[[staff:queue]] में सबसे ऊपर लाल अलर्ट का मतलब है कि "
                 "मरीज़ ने ख़तरे का कोई लक्षण चुना या मदद माँगी। तुरंत मरीज़ के पास जाएँ, फिर "
                 "[[staff:qbAcknowledge]] दबाएँ। [[staff:qbSound]] चालू रखें।"),
                ("जो कियोस्क नहीं जाँच सकता", "[[staff:nurseCheckShort]]: ख़तरे के लक्षण की "
                 "पुष्टि मरीज़ से मिलकर करें। मरीज़ की जगह बदलने के लिए ⋯ → "
                 "[[staff:qbChangePriority]] खोलें, कारण लिखें, फिर [[staff:qbApplyOverride]]। "
                 "विभाग की पुष्टि न हुई हो तो कारण लिखकर [[staff:qbConfirmDepartment]] दबाएँ।"),
                ("मरीज़ों को बुलाना", "[[staff:callNext]] बुलाने के क्रम में अगले मरीज़ को बुलाता "
                 "है, तत्काल वाले पहले। मरीज़ न आए तो उलटी गिनती पूरी होने दें, फिर "
                 "[[staff:markAbsent]]। मरीज़ [[staff:missedTitle]] में चला जाता है; "
                 "[[staff:recall]] उसे उसी टोकन के साथ उसकी जगह वापस रखता है।"),
                ("जो मरीज़ कियोस्क नहीं चला सकते", "[[staff:patients]] → "
                 "[[staff:assistedRegistration]]: पहले सहमति की सूचना मरीज़ के साथ पढ़ें, फिर "
                 "उनके साथ बैठकर उनके जवाब भरें।"),
                ("OPD कार्ड खो गया", "[[staff:patients]] → [[staff:reissueTitle]]: मरीज़ को "
                 "खोजें, उनसे उम्र और लिंग की पुष्टि करें, फिर [[staff:reissueAction]]। उन्हें "
                 "नया PIN दें; पुराना तुरंत बंद हो जाता है।"),
                ("सिस्टम बंद हो तो", "डेस्क पर रखा कागज़ी पैक इस्तेमाल करें: कागज़ का पंजीकरण "
                 "फ़ॉर्म और कागज़ी टोकन (P-001 …)। सिस्टम चालू होने पर ये विज़िट उसमें दर्ज करें।"),
                ("मदद", "IT संपर्क: ______________________ फ़ोन: ______________"),
            ],
        },
        "mr": {
            "title": "OPD Saathi · परिचारिकांसाठी कार्ड",
            "lead": "OPD Saathi किऑस्कवर रुग्णाचा इतिहास घेतो आणि रांग चालवतो. तो कधीही निदान "
            "करत नाही. आधी रुग्ण, मग स्क्रीन.",
            "steps": [
                ("साइन इन", "डेस्कच्या संगणकावर https://<server>/staff उघडा आणि स्वतःच्या ईमेल "
                 "आणि पासवर्डने साइन इन करा. पहिल्यांदा स्वतःचा पासवर्ड निवडा. तुमचे खाते कधीही "
                 "कोणाला देऊ नका."),
                ("तातडीचे अलर्ट आधी", "[[staff:queue]] मध्ये सर्वात वर लाल अलर्ट म्हणजे "
                 "रुग्णाने धोक्याचे लक्षण निवडले किंवा मदत मागितली. लगेच रुग्णाकडे जा, मग "
                 "[[staff:qbAcknowledge]] दाबा. [[staff:qbSound]] चालू ठेवा."),
                ("किऑस्क जे तपासू शकत नाही", "[[staff:nurseCheckShort]]: धोक्याच्या लक्षणाची "
                 "खात्री रुग्णाला भेटून करा. रुग्णाची जागा बदलण्यासाठी ⋯ → "
                 "[[staff:qbChangePriority]] उघडा, कारण लिहा, मग [[staff:qbApplyOverride]]. "
                 "विभागाची खात्री झाली नसेल तर कारण लिहून [[staff:qbConfirmDepartment]] दाबा."),
                ("रुग्णांना बोलावणे", "[[staff:callNext]] बोलावण्याच्या क्रमाने पुढील रुग्णाला "
                 "बोलावते, तातडीचे आधी. रुग्ण आला नाही तर उलटी मोजणी पूर्ण होऊ द्या, मग "
                 "[[staff:markAbsent]]. रुग्ण [[staff:missedTitle]] मध्ये जातो; "
                 "[[staff:recall]] त्याला त्याच टोकनसह त्याच्या जागी परत ठेवते."),
                ("जे रुग्ण किऑस्क वापरू शकत नाहीत", "[[staff:patients]] → "
                 "[[staff:assistedRegistration]]: आधी संमतीची सूचना रुग्णासोबत वाचा, मग "
                 "त्यांच्यासोबत त्यांची उत्तरे भरा."),
                ("OPD कार्ड हरवले", "[[staff:patients]] → [[staff:reissueTitle]]: रुग्णाला "
                 "शोधा, त्यांच्याकडून वय आणि लिंग तपासा, मग [[staff:reissueAction]]. त्यांना "
                 "नवीन PIN द्या; जुना लगेच बंद होतो."),
                ("सिस्टम बंद असेल तर", "डेस्कवरील कागदी पॅक वापरा: कागदी नोंदणी फॉर्म आणि "
                 "कागदी टोकन (P-001 …). सिस्टम सुरू झाल्यावर या भेटी त्यात नोंदवा."),
                ("मदत", "IT संपर्क: ______________________ फोन: ______________"),
            ],
        },
    },
    "doctor": {
        "en": {
            "title": "OPD Saathi · doctor's card",
            "lead": "The kiosk takes the history in the patient's own words. It never "
            "diagnoses; every clinical decision is yours.",
            "steps": [
                ("Sign in", "Open https://<server>/staff with your own email and password. "
                 "The first time, choose your own password."),
                ("Call your next patient", "In [[staff:queue]], [[staff:callNext]] calls the "
                 "next patient, urgent first. Then [[staff:openCaseSheet]] and "
                 "[[staff:startConsult]]."),
                ("Read what the patient said", "[[staff:tabOverview]] shows the complaint, red "
                 "flags and history; [[staff:tabTranscript]] has every answer as given. "
                 "Anything the patient or a nurse changed is marked."),
                ("AI drafts are only drafts", "Check a draft before using it: tap a source to "
                 "see the answer it rests on, then [[staff:aiAccept]], [[staff:aiEdit]] or "
                 "[[staff:aiReject]]. AI never diagnoses or prescribes."),
                ("Your own notes", "[[staff:tabNotes]]: examination findings, assessment, plan "
                 "and follow-up date, then [[staff:doctorNotesSave]]. Each save is a new "
                 "version, printed on the case sheet."),
                ("Old papers", "[[staff:tabPapers]]: photos the patient brought. Accept or "
                 "correct each reading before it counts as a result."),
                ("Finish", "[[staff:printCaseSheet]] for the case sheet, then "
                 "[[staff:finishConsult]]. If the patient does not come: "
                 "[[staff:markAbsent]]."),
                ("Keyboard", "N: [[staff:shortcutQueue]] · E: [[staff:shortcutEdit]] · "
                 "P: [[staff:shortcutPrint]]"),
            ],
        },
        "hi": {
            "title": "OPD Saathi · डॉक्टर के लिए कार्ड",
            "lead": "कियोस्क मरीज़ के अपने शब्दों में इतिहास लेता है। यह निदान कभी नहीं करता; हर "
            "चिकित्सकीय निर्णय आपका है।",
            "steps": [
                ("साइन इन", "https://<server>/staff खोलें और अपने ईमेल और पासवर्ड से साइन इन "
                 "करें। पहली बार अपना पासवर्ड खुद चुनें।"),
                ("अगले मरीज़ को बुलाएँ", "[[staff:queue]] में [[staff:callNext]] अगले मरीज़ को "
                 "बुलाता है, तत्काल वाले पहले। फिर [[staff:openCaseSheet]] और "
                 "[[staff:startConsult]]।"),
                ("मरीज़ ने क्या कहा", "[[staff:tabOverview]] में शिकायत, ख़तरे के संकेत और इतिहास "
                 "है; [[staff:tabTranscript]] में हर जवाब वैसा ही है जैसा दिया गया। मरीज़ या नर्स "
                 "ने जो बदला, वह चिह्नित है।"),
                ("AI का मसौदा केवल मसौदा है", "इस्तेमाल से पहले जाँचें: किसी स्रोत पर टैप करके "
                 "वह जवाब देखें जिस पर वाक्य आधारित है, फिर [[staff:aiAccept]], "
                 "[[staff:aiEdit]] या [[staff:aiReject]]। AI न निदान करता है, न दवा लिखता है।"),
                ("आपके अपने नोट्स", "[[staff:tabNotes]]: जाँच के निष्कर्ष, आकलन, योजना और "
                 "फ़ॉलो-अप की तारीख, फिर [[staff:doctorNotesSave]]। हर बार सहेजने पर नया संस्करण "
                 "बनता है, जो केस शीट पर छपता है।"),
                ("पुराने कागज़", "[[staff:tabPapers]]: मरीज़ के लाए कागज़ों की फ़ोटो। हर रीडिंग "
                 "को परिणाम मानने से पहले स्वीकार करें या ठीक करें।"),
                ("समाप्त करें", "केस शीट के लिए [[staff:printCaseSheet]], फिर "
                 "[[staff:finishConsult]]। मरीज़ न आए तो: [[staff:markAbsent]]।"),
                ("कीबोर्ड", "N: [[staff:shortcutQueue]] · E: [[staff:shortcutEdit]] · "
                 "P: [[staff:shortcutPrint]]"),
            ],
        },
        "mr": {
            "title": "OPD Saathi · डॉक्टरांसाठी कार्ड",
            "lead": "किऑस्क रुग्णाच्या स्वतःच्या शब्दांत इतिहास घेतो. तो कधीही निदान करत नाही; "
            "प्रत्येक वैद्यकीय निर्णय तुमचा आहे.",
            "steps": [
                ("साइन इन", "https://<server>/staff उघडा आणि स्वतःच्या ईमेल आणि पासवर्डने साइन "
                 "इन करा. पहिल्यांदा स्वतःचा पासवर्ड निवडा."),
                ("पुढील रुग्णाला बोलवा", "[[staff:queue]] मध्ये [[staff:callNext]] पुढील "
                 "रुग्णाला बोलावते, तातडीचे आधी. मग [[staff:openCaseSheet]] आणि "
                 "[[staff:startConsult]]."),
                ("रुग्ण काय म्हणाले", "[[staff:tabOverview]] मध्ये तक्रार, धोक्याची चिन्हे आणि "
                 "इतिहास आहे; [[staff:tabTranscript]] मध्ये प्रत्येक उत्तर दिल्याप्रमाणे आहे. "
                 "रुग्णाने किंवा परिचारिकेने जे बदलले ते चिन्हांकित आहे."),
                ("AI चा मसुदा फक्त मसुदा आहे", "वापरण्यापूर्वी तपासा: स्रोतावर टॅप करून ते "
                 "उत्तर पाहा ज्यावर वाक्य आधारित आहे, मग [[staff:aiAccept]], "
                 "[[staff:aiEdit]] किंवा [[staff:aiReject]]. AI निदान करत नाही, औषधही लिहीत "
                 "नाही."),
                ("तुमच्या स्वतःच्या नोंदी", "[[staff:tabNotes]]: तपासणीतील निष्कर्ष, मूल्यांकन, "
                 "योजना आणि फॉलो-अपची तारीख, मग [[staff:doctorNotesSave]]. प्रत्येक वेळी जतन "
                 "केल्यावर नवीन आवृत्ती बनते, जी केस शीटवर छापली जाते."),
                ("जुनी कागदपत्रे", "[[staff:tabPapers]]: रुग्णाने आणलेल्या कागदांचे फोटो. "
                 "प्रत्येक वाचन निकाल मानण्यापूर्वी स्वीकारा किंवा दुरुस्त करा."),
                ("पूर्ण करा", "केस शीटसाठी [[staff:printCaseSheet]], मग "
                 "[[staff:finishConsult]]. रुग्ण आला नाही तर: [[staff:markAbsent]]."),
                ("कीबोर्ड", "N: [[staff:shortcutQueue]] · E: [[staff:shortcutEdit]] · "
                 "P: [[staff:shortcutPrint]]"),
            ],
        },
    },
    "admin": {
        "en": {
            "title": "OPD Saathi · administrator's card",
            "lead": "You keep OPD Saathi safe to use: who can sign in, which kiosks work, "
            "which features are on, and that backups run.",
            "steps": [
                ("Sign in safely", "Two-step sign-in with an authenticator app is required. "
                 "Keep the ten recovery codes locked away. [[staff:myAccount]] shows where you "
                 "are signed in."),
                ("Staff accounts", "[[staff:settings]] → [[staff:adminUsers]] → "
                 "[[staff:adminCreateUsers]]: one account per person, with their department. "
                 "Use [[staff:resetPassword]] for a forgotten password and "
                 "[[staff:signOutEverywhere]] when someone leaves or loses a device."),
                ("Kiosks and phone check-in", "[[staff:adminKiosks]] → "
                 "[[staff:adminCreateKiosks]], then open its setup link once on the kiosk. "
                 "[[staff:checkinQrTitle]]: print it for the waiting area."),
                ("Switches", "[[staff:adminDepartments]]: [[staff:interviewSettingsTitle]], "
                 "[[staff:aiSettingsTitle]] and [[staff:featureSettingsTitle]]. Turn a feature "
                 "on only once the hospital has approved it."),
                ("Every morning", "[[staff:system]]: database, Redis and worker up, the last "
                 "backup within a day, enough disk space. Alerts also reach IT by email."),
                ("Who did what", "[[staff:auditLog]]: sign-ins, record views and changes, "
                 "with who and when."),
                ("Reports", "[[staff:reports]]: visits, waiting times and AI use, without "
                 "patient names."),
                ("When something fails", "The runbooks in docs/runbooks say what to do for "
                 "each alert. If the server is down in OPD hours, the desk switches to the "
                 "paper pack."),
            ],
        },
        "hi": {
            "title": "OPD Saathi · प्रशासक के लिए कार्ड",
            "lead": "OPD Saathi को सुरक्षित रखना आपका काम है: कौन साइन इन कर सकता है, कौन से "
            "कियोस्क चलते हैं, कौन सी सुविधाएँ चालू हैं, और बैकअप चल रहे हैं या नहीं।",
            "steps": [
                ("सुरक्षित साइन इन", "ऑथेंटिकेटर ऐप से दो-चरणीय साइन-इन ज़रूरी है। दस रिकवरी "
                 "कोड ताले में रखें। [[staff:myAccount]] दिखाता है कि आप कहाँ-कहाँ साइन इन हैं।"),
                ("स्टाफ़ खाते", "[[staff:settings]] → [[staff:adminUsers]] → "
                 "[[staff:adminCreateUsers]]: हर व्यक्ति का अपना खाता, उसके विभाग के साथ। भूले "
                 "पासवर्ड के लिए [[staff:resetPassword]], और कोई जाए या डिवाइस खो दे तो "
                 "[[staff:signOutEverywhere]]।"),
                ("कियोस्क और फ़ोन चेक-इन", "[[staff:adminKiosks]] → "
                 "[[staff:adminCreateKiosks]], फिर उसका सेटअप लिंक एक बार कियोस्क पर खोलें। "
                 "[[staff:checkinQrTitle]]: इसे प्रतीक्षा कक्ष के लिए छापें।"),
                ("स्विच", "[[staff:adminDepartments]]: [[staff:interviewSettingsTitle]], "
                 "[[staff:aiSettingsTitle]] और [[staff:featureSettingsTitle]]। कोई सुविधा तभी "
                 "चालू करें जब अस्पताल ने उसे मंज़ूरी दी हो।"),
                ("हर सुबह", "[[staff:system]]: डेटाबेस, Redis और वर्कर चालू, आख़िरी बैकअप एक "
                 "दिन के भीतर, पर्याप्त डिस्क जगह। अलर्ट ईमेल से IT तक भी पहुँचते हैं।"),
                ("किसने क्या किया", "[[staff:auditLog]]: साइन-इन, रिकॉर्ड देखना और बदलाव, किसने "
                 "और कब।"),
                ("रिपोर्ट", "[[staff:reports]]: विज़िट, प्रतीक्षा समय और AI का उपयोग, मरीज़ों के "
                 "नाम के बिना।"),
                ("कुछ बिगड़ जाए तो", "docs/runbooks की रनबुक हर अलर्ट पर क्या करना है बताती हैं। "
                 "OPD के समय सर्वर बंद हो तो डेस्क कागज़ी पैक पर चला जाता है।"),
            ],
        },
        "mr": {
            "title": "OPD Saathi · प्रशासकांसाठी कार्ड",
            "lead": "OPD Saathi सुरक्षित ठेवणे हे तुमचे काम आहे: कोण साइन इन करू शकते, कोणते "
            "किऑस्क चालतात, कोणत्या सुविधा चालू आहेत आणि बॅकअप होत आहेत की नाही.",
            "steps": [
                ("सुरक्षित साइन इन", "ऑथेंटिकेटर ॲपसह दोन-टप्प्यांचे साइन-इन आवश्यक आहे. दहा "
                 "रिकव्हरी कोड कुलूपबंद ठेवा. [[staff:myAccount]] तुम्ही कुठे कुठे साइन इन आहात "
                 "ते दाखवते."),
                ("कर्मचारी खाती", "[[staff:settings]] → [[staff:adminUsers]] → "
                 "[[staff:adminCreateUsers]]: प्रत्येक व्यक्तीचे स्वतःचे खाते, त्यांच्या "
                 "विभागासह. विसरलेल्या पासवर्डसाठी [[staff:resetPassword]], आणि कोणी सोडून गेले "
                 "किंवा डिव्हाइस हरवले तर [[staff:signOutEverywhere]]."),
                ("किऑस्क आणि फोन चेक-इन", "[[staff:adminKiosks]] → "
                 "[[staff:adminCreateKiosks]], मग त्याची सेटअप लिंक एकदा किऑस्कवर उघडा. "
                 "[[staff:checkinQrTitle]]: प्रतीक्षा कक्षासाठी छापा."),
                ("स्विच", "[[staff:adminDepartments]]: [[staff:interviewSettingsTitle]], "
                 "[[staff:aiSettingsTitle]] आणि [[staff:featureSettingsTitle]]. रुग्णालयाने "
                 "मंजुरी दिल्यावरच एखादी सुविधा चालू करा."),
                ("दररोज सकाळी", "[[staff:system]]: डेटाबेस, Redis आणि वर्कर चालू, शेवटचा बॅकअप "
                 "एका दिवसाच्या आत, पुरेशी डिस्क जागा. अलर्ट ईमेलने IT पर्यंतही पोहोचतात."),
                ("कोणी काय केले", "[[staff:auditLog]]: साइन-इन, नोंदी पाहणे आणि बदल, कोणी आणि "
                 "केव्हा."),
                ("अहवाल", "[[staff:reports]]: भेटी, प्रतीक्षेचा वेळ आणि AI चा वापर, रुग्णांच्या "
                 "नावांशिवाय."),
                ("काही बिघडले तर", "docs/runbooks मधील रनबुक प्रत्येक अलर्टवर काय करायचे ते "
                 "सांगतात. OPD वेळेत सर्व्हर बंद असेल तर डेस्क कागदी पॅकवर जातो."),
            ],
        },
    },
}

# "How to check in": the kiosk's own words for each step.
CHECK_IN = {
    "en": {
        "title": "How to check in",
        "steps": [
            "[[kiosk:chooseLanguage]].",
            "Press [[kiosk:startIntake]].",
            "Read the notice, then press [[intake:consentAgree]].",
            "Answer the questions: tap, speak, or [[intake:typeInstead]].",
            "Check your answers, then press [[intake:confirm]].",
            "Take your token number and wait to be called.",
        ],
        "urgent": "If you feel very unwell, tell the staff now.",
    },
    "hi": {
        "title": "चेक-इन कैसे करें",
        "steps": [
            "[[kiosk:chooseLanguage]]।",
            "[[kiosk:startIntake]] दबाएँ।",
            "सूचना पढ़ें, फिर [[intake:consentAgree]] दबाएँ।",
            "सवालों के जवाब दें: टैप करें, बोलें, या [[intake:typeInstead]]।",
            "अपने जवाब जाँचें, फिर [[intake:confirm]] दबाएँ।",
            "अपना टोकन नंबर लें और बुलाए जाने तक प्रतीक्षा करें।",
        ],
        "urgent": "अगर तबीयत बहुत ख़राब लगे, तो अभी स्टाफ़ को बताएँ।",
    },
    "mr": {
        "title": "चेक-इन कसे करावे",
        "steps": [
            "[[kiosk:chooseLanguage]].",
            "[[kiosk:startIntake]] दाबा.",
            "सूचना वाचा, मग [[intake:consentAgree]] दाबा.",
            "प्रश्नांची उत्तरे द्या: टॅप करा, बोला, किंवा [[intake:typeInstead]].",
            "तुमची उत्तरे तपासा, मग [[intake:confirm]] दाबा.",
            "तुमचा टोकन नंबर घ्या आणि बोलावले जाईपर्यंत थांबा.",
        ],
        "urgent": "खूप अस्वस्थ वाटत असल्यास, आत्ताच कर्मचाऱ्यांना सांगा.",
    },
}


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def strings(language: str, namespace: str) -> dict[str, str]:
    path = LOCALES / language / f"{namespace}.json"
    return {key: value for key, value in json.loads(path.read_text(encoding="utf-8")).items()
            if isinstance(value, str)}


def fill(language: str, text: str) -> str:
    """Escape the text and put each [[namespace:key]] in as the screen's own label."""
    out, last = [], 0
    for match in LABEL.finditer(text):
        out.append(esc(text[last:match.start()]))
        namespace, key = match.groups()
        labels = strings(language, namespace)
        if key not in labels:
            raise SystemExit(f"apps/web/src/locales/{language}/{namespace}.json has no {key}")
        out.append(f'<b class="ui">{esc(labels[key])}</b>')
        last = match.end()
    out.append(esc(text[last:]))
    return "".join(out)


def page(language: str, title: str, body: str, extra_class: str = "") -> str:
    attribute = f' class="{extra_class}"' if extra_class else ""
    return (
        f'<!doctype html>\n<html lang="{language}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{esc(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body{attribute}>\n"
        f"{body}\n</body>\n</html>\n"
    )


def card(role: str, language: str) -> str:
    content = CARDS[role][language]
    steps = "".join(
        f"<li><h2>{esc(heading)}</h2><p>{fill(language, text)}</p></li>"
        for heading, text in content["steps"]  # type: ignore[union-attr]
    )
    body = (
        f'<h1>{esc(str(content["title"]))}</h1>\n<p class="lead">{esc(str(content["lead"]))}</p>\n'
        f'<ol class="card">{steps}</ol>\n'
        f'<p class="footer">OPD Saathi · docs/print · scripts/make_print_pack.py · {role} · '
        f"{language}</p>"
    )
    return page(language, str(content["title"]), body)


def check_in_sign() -> str:
    sections = "".join(
        f'<section lang="{language}"><h1>{esc(CHECK_IN[language]["title"])}</h1><ol>'
        + "".join(f"<li>{fill(language, step)}</li>" for step in CHECK_IN[language]["steps"])
        + f'</ol><p class="urgent">{esc(CHECK_IN[language]["urgent"])}</p></section>'
        for language in LANGUAGES
    )
    return page("en", CHECK_IN["en"]["title"], f'<div class="sign">{sections}</div>', "")


def unwell_sign() -> str:
    sections = "".join(
        f'<section lang="{language}"><h1>{esc(CHECK_IN[language]["urgent"])}</h1></section>'
        for language in LANGUAGES
    )
    return page("en", CHECK_IN["en"]["urgent"], f'<div class="sign unwell">{sections}</div>')


def build() -> dict[str, str]:
    files = {
        f"staff-card-{role}-{language}.html": card(role, language)
        for role in CARDS
        for language in LANGUAGES
    }
    files["sign-how-to-check-in.html"] = check_in_sign()
    files["sign-if-you-feel-unwell.html"] = unwell_sign()
    return files


def main() -> int:
    files = build()
    if "--check" in sys.argv:
        stale = [name for name, text in files.items()
                 if not (OUT / name).is_file() or (OUT / name).read_text(encoding="utf-8") != text]
        if stale:
            print("docs/print is out of date; run python scripts/make_print_pack.py:", *stale)
            return 1
        print("docs/print matches the app's translations.")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {len(files)} files to docs/print.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
