# OPD Saathi — demo guide

Fictional patients only. The app gathers history for clinical staff; it does not diagnose or recommend treatment. Question banks, red-flag rules and translations are drafts awaiting clinician and native-speaker review ([CLINICIAN_REVIEW.md](CLINICIAN_REVIEW.md)).

## 1. Start everything

```powershell
powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
```

The script prints a kiosk activation link and staff logins and opens the kiosk in your browser. Use **Chrome or Edge** (voice needs them). Put `GEMINI_API_KEY` (and optionally `SARVAM_API_KEY`) in `.env` before starting to show the AI features; the script says which features are on.

Recommended screen setup: kiosk in one window (full screen, F11), staff workspace logged in as `doctor01` in a second window, and a phone-sized window for the QR status page.

## 2. Three-minute walkthrough

| Time | Show | Say |
|---|---|---|
| 0:00 | Kiosk welcome. Tap **मराठी** then **Start**. | "A patient walks up to the kiosk and picks their language. Everything from here is in Marathi." |
| 0:15 | Consent is read aloud automatically. Tap **I agree**. Enter age, tap sex. | "Consent first. Only age and sex are needed; a phone number is optional for SMS updates." |
| 0:35 | Safety check: tap **No, none of these**. | "Danger signs are asked first, so a chest-pain patient alerts the nurse within seconds." |
| 0:45 | Questions: each question is read aloud. Tap **ताप** (fever) and **खोकला** (cough). On the next questions tap answers; on one question tap **Tap and speak** and answer in Marathi. | "Most answers are one tap. The patient can also just speak. Follow-up questions adapt to the complaint — fever and cough bring fever and breathing questions." |
| 1:30 | On the medicines question, tap **Scan old prescription** and photograph a prescription (needs Gemini). | "Old paper prescriptions are read automatically; the patient confirms." |
| 1:45 | Review screen: the read-back is spoken in Marathi. Tap **Yes, this is correct**. | "The patient hears and confirms their history before it reaches the doctor." |
| 2:00 | Token screen: big token, department, place in queue, QR. Scan the QR with a phone. | "They get a token and can follow their turn on their phone." |
| 2:15 | Staff window (doctor01): queue shows the new token with the complaint in English. Open the case sheet. | "The doctor gets a one-page English case sheet: AI brief on top, structured history, and the patient's own Marathi words under each line. Anything can be edited." |
| 2:45 | Back on the kiosk start a new check-in, tap **Chest discomfort** on the safety screen. The nurse queue shows an urgent alert instantly. Press the **phone icon** (Call staff) on the kiosk: a "Help requested" alert appears for staff. | "Urgent cases and patients who need help reach staff in real time." |

## 3. What runs where

| Feature | With Gemini key | Without keys |
|---|---|---|
| Doctor's English case sheet | Full AI translation of every answer + 2–3 line brief | Tapped answers are translated from the bilingual question bank; typed/spoken words stay in the patient's language |
| Voice answers | Server speech-to-text (Sarvam if its key is set, else Gemini) | Browser speech recognition (Chrome/Edge, needs internet) |
| Read-aloud | Natural voice (Sarvam or Gemini) | Browser voice (Marathi falls back to a Hindi voice) |
| Prescription photo | Read automatically | Button hidden |
| Safety alerts, routing, queue, tokens, analytics | Rules engine (+ AI second opinion that can only raise urgency) | Rules engine |

## 4. Useful commands

```powershell
docker compose run --rm api python -m app.cli demo-kiosk --web-url http://localhost:5173   # new kiosk link
docker compose run --rm api python -m app.cli seed-demo-data   # five sample visits for the staff screens
docker compose logs -f api                                     # watch the API (AI calls log as llm_*)
docker compose down                                            # stop
```

Unfinished visits from earlier days are closed automatically (waiting → no-show), so each morning starts with an empty queue.
