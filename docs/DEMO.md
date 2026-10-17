# OPD Saathi — demo script (IIT Bombay)

Fictional patients only. OPD Saathi collects a history for the clinical team; it does not
diagnose or recommend treatment. Question banks, red-flag rules, the Ayurveda module and all
translations are drafts awaiting clinician and native-speaker review
([CLINICIAN_REVIEW.md](CLINICIAN_REVIEW.md)).

The story is one returning patient, Sunita (fictional, 52), told from four screens: the kiosk
she uses, her phone, the nurse's queue board and the doctor's consultation. It takes about ten
minutes. Section 4 adds an emergency (one minute) and section 5 says what to do when
something fails.

## 1. Before the session (30 minutes ahead)

1. Start Docker Desktop, then run, from the repository root:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
   ```

   It builds, migrates, seeds the demo hospital and a month of history for Reports, resets the
   returning patient, and opens the kiosk in your default browser, already activated. Note
   from its output: the **staff logins** and the line **Returning patient: OPD number …,
   PIN …** (her card for today). To run the kiosk in another browser, open the printed
   activation link there instead (each link works once).
2. Run the pre-demo check and fix anything marked `[warn]`:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1
   powershell -ExecutionPolicy Bypass -File .\scripts\lan-check.ps1
   ```

   `demo-check` proves that Gemini and the voice service answer right now (one tiny request,
   no patient data). `lan-check` checks that phones on the same Wi-Fi can reach this PC; finish
   by opening the link it prints on the demo phone.
3. Set up the screens (Chrome or Edge, which voice needs):

   | Window | Open | Sign in |
   |---|---|---|
   | Kiosk (full screen, F11) | the tab `start-demo` opened (`/kiosk`) | — |
   | Nurse | `/staff/queue` | `nurse01@demo.opdsathi.test` |
   | Doctor | `/staff/consultations` | `doctor01@demo.opdsathi.test` (General Medicine) |
   | Phone | — (scans the token QR later) | — |

4. Have her old test report ready to photograph: print
   `docs/demo-papers/synthetic-lab-report.png`, or show it on a second screen. On a laptop
   without a camera, the photo button opens a file picker; choose that file.
5. Volume up; allow the microphone when the kiosk asks.

## 2. The run-through

Labels are shown in English with the Marathi the kiosk displays.

| Time | Screen | Do | Say |
|---|---|---|---|
| 0:00 | Kiosk | Tap **मराठी**, then **Start patient check-in** (रुग्णाची नोंदणी सुरू करा). | "Sunita has come back to the OPD. She picks her language; everything from here is in Marathi, and every screen is read aloud." |
| 0:20 | Kiosk | Consent is read aloud; tap **I agree** (मी सहमत आहे, पुढे चला). | "First the consent for her care: what is collected and who sees it." |
| 0:35 | Kiosk | The separate AI question; tap **Yes, use AI help** (होय, AI ची मदत घ्या). | "AI help is a separate, optional choice, with nothing pre-selected. If she says no, the visit works exactly the same on this server alone: tapping and typing, with no online service. She can stop AI help later from her phone." |
| 0:55 | Kiosk | Age **52**, **Female**, **Next**. Then **Yes, I have my OPD card** (हो, माझ्याकडे ओपीडी कार्ड आहे); type the OPD number and PIN from `start-demo`. Then **Yes, the same problem** (हो, तोच त्रास). | "Her OPD card from the last visit links today to her record. The kiosk never shows that record; it only asks whether today is a follow-up." |
| 1:40 | Kiosk | Safety screen: **No, none of these** (नाही, यापैकी काहीही नाही). | "Danger signs come first, so a chest-pain patient reaches the nurse within seconds." |
| 1:55 | Kiosk | Answer the questions: mostly taps. On one, tap **Tap and speak** (दाबा आणि बोला) and answer in Marathi, e.g. "दोन आठवड्यांपासून थकवा आहे". | "One tap per answer, or she simply speaks. Follow-up questions adapt to her complaint. 'Don't know' and 'prefer not to say' are kept apart from 'no'." |
| 3:15 | Kiosk | Review screen: the read-back plays in Marathi. Under **Old papers** (जुनी कागदपत्रे), tap **Test report** (तपासणी अहवाल) and photograph the report. The photo appears. | "She hears her answers back before anything reaches the doctor. She also photographs the test report she brought. It's stored encrypted with today's visit, and with her consent AI reads it for the doctor." |
| 3:50 | Kiosk | Tap **Yes, this is correct** (हो, हे बरोबर आहे). | "Confirmed: from now on her answers are fixed. Any change goes through the desk or the doctor." |
| 4:00 | Kiosk + phone | The token screen: token, department, place in queue, QR and her OPD card. Scan the QR with the phone. | "She gets a token and follows her turn on her own phone. This page also has **Stop AI help**, which can only stop AI help, never start it." |
| 4:30 | Nurse | The queue board shows her token, her complaint in English and her wait time. | "The nurse sees everyone waiting, live, with urgency first. A missed call can be recalled; the patient keeps their place." |
| 5:00 | Doctor | In **Consultations**, open her token from **Today's patients**, tap **Call patient**, then **Start consultation**. Overview: the AI brief. Tap a source chip under a sentence; it opens her answer in **Interview transcript**. Back on Overview, **Accept**. | "The doctor gets a one-page English case sheet. The AI brief is a draft: every sentence cites the answers it rests on, and the doctor accepts, edits or rejects it." |
| 6:00 | Doctor | **Previous visits** tab. | "Her visit three weeks ago: complaint, medicines, a penicillin allergy. These are shown only while she is under this doctor's care today, and every read is audited." |
| 6:30 | Doctor | **Old papers** tab: today's report beside what AI read. Point at Haemoglobin: **Below the printed range · Marked low on the report**. Tap the photo to enlarge it, then **Matches the photo**. Below, **From earlier visits**: the prescription from three weeks ago (a photo only). | "AI copied the report; it did not judge it. The flags are plain arithmetic against the range printed on this same paper, never a general range. A text result like 'No growth' is never flagged. The doctor compares the reading with the photo and decides." |
| 7:30 | Doctor | **Finish consultation**; optionally **Download case sheet PDF**. | "Everything the doctor changed is versioned and audited." |
| 8:00 | Admin (optional) | Sign in as the admin; **Reports** (30 days of fictional history), then **Settings → Departments** (the Ayurveda history switch) and **Settings → Kiosks → Show QR** (phone check-in). | "The administrator sees the OPD's flow and waits. AYUSH hospitals switch on the Ayurveda history, and patients can check in from their own phones by scanning a poster." |

## 3. Points to make

- **Safety before speed.** The rules engine raises urgency; AI can only add a second opinion
  that raises priority, never lowers it. Urgency never falls without a recorded staff decision.
- **Consent by purpose.** Care consent is required; AI help is optional and can be stopped from
  the patient's phone or at the desk. After that nothing more goes to AI services and the doctor
  no longer sees AI drafts.
- **Privacy.** Names, phones, case sheets, transcripts and paper photos are encrypted in the
  database. Every hospital's data is separated by row-level security. Each route that names a
  record is tested against other hospitals, other departments and other patients' sessions.
- **AI as a checked draft.** Briefs cite their sources, readings of papers sit beside the photo,
  and nothing AI produces reaches the record until a doctor accepts it.
- **Honest limits.** The interview and rules are drafts awaiting clinical review. The system
  does not diagnose. ABHA/ABDM integration and interaction checks are documented as future
  work, not shown.

## 4. Emergency (optional, one minute)

On the kiosk start a new check-in (any age, no OPD card), and on the safety screen tap **Chest
discomfort** (छातीत त्रास), then **Continue**. The nurse's board shows an urgent alert at once.
Finish the questions: after confirmation the patient is first in the queue, marked **Nurse to
confirm**, and the nurse acknowledges the alert after seeing the patient. (**Get help now**
instead stops the questions and shows the emergency screen; the alert is the same.) Say: "A
self-reported danger sign is never suppressed, but a nurse confirms it in person, so the button
cannot be used to jump the queue unnoticed."

## 5. If something fails

| What happens | Do | Say |
|---|---|---|
| `demo-check` says Gemini did not answer (no internet) | Continue; at the AI question tap **No, continue without AI**. Skip the old-paper reading; the photo is still kept. | "Without AI the visit runs on this server alone: the rules, tapping and typing, and recorded question audio." |
| The paper reading still says "AI is reading this paper" after 30 seconds | Open the photo and read it yourself. | "The photo is the record; the AI reading is only ever a draft." |
| Voice answer not heard | Tap **Type instead**, or tap an answer. | "Voice is optional; every question can be tapped." |
| Microphone blocked | Allow it in the browser's address bar, or tap answers. | — |
| The phone cannot open the QR | Run `.\scripts\lan-check.ps1`; meanwhile show the status page on the PC (`/s/…` from the token screen). | — |
| OPD card refused | Check age 52 and Female, then the number and PIN from `start-demo`. Or tap **No, or I don't have my card**; the nurse can link her later from **Previous visits → Find an earlier record**. | "A wrong card is refused without saying which part was wrong." |
| A screen looks out of date after a rebuild | Reload it once; the app takes the new version and reloads open pages itself from then on. | — |
| Kiosk shows "Activate this kiosk" | Run `docker compose run --rm api python -m app.cli demo-kiosk --web-url <web address>` and open the new link once. | — |
| Anything else | `docker compose logs -f api` shows errors (never patient data). Restart with `.\start-demo.ps1 -NoBuild`. | — |

## 6. Before the next run-through

`start-demo.ps1` already resets the returning patient. Without a restart, run:

```powershell
docker compose run --rm api python -m app.cli seed-demo-returning --reset
```

This removes the visits joined to her record by earlier run-throughs and keeps her OPD card.
Tokens from a rehearsal stay on today's board; finish them from the doctor's screen, or leave
them as a believable queue.

## 7. What runs where

| Feature | With Gemini (and Sarvam) keys | Without keys, or without the patient's AI consent |
|---|---|---|
| Case sheet for the doctor | Translation of every answer, plus a cited AI brief | Tapped answers translated from the bilingual question bank; spoken or typed words stay in the patient's language |
| Voice answers | Server speech recognition (Sarvam, else Gemini) | Tap and type (with consent but no keys: the browser's recogniser) |
| Read-aloud | Natural voices (Sarvam or Gemini) | Recorded question audio; the patient's own words only with a voice installed on the device |
| Old papers | Photo kept, and read into a draft the doctor checks | Photo kept, not read |
| Safety alerts, routing, queue, tokens, history, reports | Rules engine (and an AI second opinion that can only raise urgency) | Rules engine |

## 8. Useful commands

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1        # is everything ready?
docker compose run --rm api python -m app.cli seed-demo-returning --reset # fresh returning patient
docker compose run --rm api python -m app.cli ai-check                   # do AI and voice answer?
docker compose run --rm api python -m app.cli seed-demo-data             # five sample patients in today's queue
docker compose run --rm api python -m app.cli clear-demo-data            # remove those five
docker compose logs -f api                                               # watch the API
docker compose down                                                      # stop everything
```

Unfinished visits from earlier days are closed automatically (waiting becomes no-show), so
each morning starts with an empty queue.
