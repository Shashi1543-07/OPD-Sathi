import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { QRCodeSVG } from "qrcode.react";
import {
  AlertTriangle,
  Camera,
  Check,
  ChevronLeft,
  IdCard,
  Keyboard,
  Languages,
  Loader2,
  LogOut,
  Mic,
  Square,
  Volume2,
} from "lucide-react";
import { ConfirmDialog } from "../components/ui";
import { EmergencyNotice } from "./EmergencyNotice";
import { KioskPapers } from "./KioskPapers";
import {
  intake,
  IntakeRequestError,
  type ConsentCopy,
  type DangerTile as DangerOption,
  type IntakeResponse,
  type OpdCard,
  type Question,
  useKioskHospitalName,
} from "../lib/intake";
import { useLanguageStore } from "../languageStore";
import type { Language } from "../i18n";
import { useKioskPrefs } from "../lib/kioskPrefs";
import { shrinkPhoto } from "../lib/photos";
import {
  browserRecognitionAvailable,
  listenInBrowser,
  listenWithServer,
  recordingAvailable,
  secureVoiceAvailable,
  speak,
  stopSpeaking,
  type Listener,
  type SpeechCapabilities,
} from "../lib/voice";
import "./intake.css";

type Stage =
  | "consent"
  /** The separate, optional question about online AI help. */
  | "ai"
  | "identity"
  | "returning"
  | "danger"
  | "interview"
  | "summary"
  | "done"
  | "help"
  | "emergency";
type Answered = Question & { answer: string | number | null };
type QueueTicket = {
  token: string;
  statusUrl: string;
  /** Absolute link a phone on the clinic Wi-Fi can open; the kiosk itself is localhost. */
  publicStatusUrl: string | null;
  issuedAt: Date;
  position: number | null;
  departmentName: string | null;
  waitMinutes: number | null;
  opdCard: OpdCard | null;
};

const STEPS: { stage: Stage[]; key: string }[] = [
  { stage: ["consent", "ai"], key: "stepConsent" },
  { stage: ["identity", "returning"], key: "stepDetails" },
  { stage: ["danger"], key: "stepSafety" },
  { stage: ["interview"], key: "stepQuestions" },
  { stage: ["summary"], key: "stepReview" },
  { stage: ["done"], key: "stepToken" },
];
const RESTART_SECONDS = 60;
/** A patient idle this long on the review screen gets a nurse sent to them. */
const SUMMARY_IDLE_MS = 90_000;

/** Split a stored answer back into tapped options and free text. */
function splitAnswer(question: Question, answer: string | number | null) {
  if (typeof answer !== "string" || !answer) return { chips: [] as string[], text: "" };
  const labels = new Map((question.options ?? []).map((option) => [option.label, option.id]));
  const chips: string[] = [];
  const rest: string[] = [];
  for (const part of answer.split(",").map((item) => item.trim()).filter(Boolean)) {
    const id = labels.get(part);
    if (id) chips.push(id);
    else rest.push(part);
  }
  return { chips, text: rest.join(", ") };
}

const LANGUAGES: { code: Language; label: string }[] = [
  { code: "hi", label: "हिन्दी" },
  { code: "mr", label: "मराठी" },
  { code: "en", label: "English" },
];

/** Change language at any step; the current question is shown again in the new one. */
function LanguageSwitch({
  value,
  disabled,
  onChange,
  leading,
}: {
  value: Language;
  disabled: boolean;
  onChange: (language: Language) => void;
  /** Shown at the start of the row, e.g. the "Stop and exit" button. */
  leading?: ReactNode;
}) {
  const { t } = useTranslation("kiosk");
  return (
    <div className="intake-lang" role="group" aria-label={t("chooseLanguage")}>
      {leading}
      <Languages aria-hidden="true" size={20} />
      {LANGUAGES.map(({ code, label }) => (
        <button
          type="button"
          key={code}
          lang={code}
          aria-pressed={value === code}
          className={value === code ? "is-selected" : ""}
          disabled={disabled}
          onClick={() => onChange(code)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function Stepper({ stage }: { stage: Stage }) {
  const { t } = useTranslation("intake");
  const current = STEPS.findIndex((step) => step.stage.includes(stage));
  return (
    <ol className="intake-stepper" aria-label="Progress">
      {STEPS.map((step, index) => (
        <li
          key={step.key}
          className={index < current ? "is-done" : index === current ? "is-current" : ""}
          aria-current={index === current ? "step" : undefined}
        >
          <span className="intake-stepper-dot">
            {index < current ? <Check aria-hidden="true" size={14} /> : index + 1}
          </span>
          <span className="intake-stepper-label">{t(step.key)}</span>
        </li>
      ))}
    </ol>
  );
}

function SpeakButton({
  text,
  language,
  fetchAudio,
  autoPlayKey,
  localOnly = false,
}: {
  text: string;
  language: string;
  fetchAudio?: () => Promise<Blob>;
  /** When this value changes and voice guidance is on, read the text automatically. */
  autoPlayKey?: string;
  /** The patient's own words without consent to online help: only on-device voices. */
  localOnly?: boolean;
}) {
  const { t } = useTranslation("intake");
  const voiceGuidance = useKioskPrefs((state) => state.voiceGuidance);
  const [playing, setPlaying] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const fetchRef = useRef(fetchAudio);
  useEffect(() => {
    fetchRef.current = fetchAudio;
  }, [fetchAudio]);

  const play = useCallback(() => {
    setPlaying(true);
    setUnavailable(false);
    void speak(text, language, fetchRef.current, { localOnly })
      .then((spoken) => setUnavailable(!spoken))
      .finally(() => setPlaying(false));
  }, [text, language, localOnly]);

  useEffect(() => {
    if (!autoPlayKey || !voiceGuidance) return;
    const timer = window.setTimeout(play, 250);
    return () => {
      window.clearTimeout(timer);
      stopSpeaking();
    };
  }, [autoPlayKey, voiceGuidance, play]);

  return (
    <>
      <button
        type="button"
        className={`intake-speak ${playing ? "is-playing" : ""}`}
        onClick={() => {
          if (playing) {
            stopSpeaking();
            setPlaying(false);
          } else play();
        }}
      >
        {playing ? (
          <Square aria-hidden="true" size={18} />
        ) : (
          <Volume2 aria-hidden="true" size={20} />
        )}
        <span>{playing ? t("stopListening") : t("listen")}</span>
      </button>
      {unavailable && (
        <p className="intake-hint" role="status">
          {t("readAloudUnavailable", { ns: "common" })}
        </p>
      )}
    </>
  );
}

type VoiceStatus = "idle" | "listening" | "processing" | "heard" | "nothing" | "error";

function AnswerPanel({
  question,
  language,
  capabilities,
  initial,
  busy,
  editing,
  canGoBack,
  onBack,
  onSubmit,
}: {
  question: Question;
  language: string;
  capabilities?: SpeechCapabilities;
  initial: string | number | null;
  busy: boolean;
  editing: boolean;
  canGoBack: boolean;
  onBack: () => void;
  onSubmit: (answer: string | number | null, mode: "touch" | "voice", skip: boolean) => void;
}) {
  const { t } = useTranslation("intake");
  const seeded = useMemo(() => splitAnswer(question, initial), [question, initial]);
  const [chips, setChips] = useState<string[]>(seeded.chips);
  const [text, setText] = useState(seeded.text);
  const [scale, setScale] = useState<number | undefined>(
    typeof initial === "number" ? initial : undefined,
  );
  const [showText, setShowText] = useState(Boolean(seeded.text));
  const [voice, setVoice] = useState<VoiceStatus>("idle");
  const [voiceMessage, setVoiceMessage] = useState("");
  const [usedVoice, setUsedVoice] = useState(false);
  const [level, setLevel] = useState(0);
  const [scanning, setScanning] = useState(false);
  const listener = useRef<Listener | null>(null);
  const photoInput = useRef<HTMLInputElement>(null);
  const canScan = question.id === "medications" && capabilities?.ai === true;

  async function scanPrescription(file: File) {
    setScanning(true);
    setVoiceMessage(t("scanning"));
    try {
      const medicines = await intake.readPrescription(await shrinkPhoto(file));
      if (!medicines.length) {
        setVoiceMessage(t("scanNone"));
        return;
      }
      const found = medicines
        .map((item) => [item.name, item.dose, item.how_often].filter(Boolean).join(" "))
        .join(", ");
      setShowText(true);
      setText((current) => (current ? `${current}, ${found}` : found));
      setVoiceMessage(t("scanFound"));
    } catch (cause) {
      const status = cause instanceof IntakeRequestError ? cause.status : 0;
      setVoiceMessage(
        t(status === 413 ? "scanTooLarge" : status === 415 ? "scanNotPhoto" : "scanFailed"),
      );
    } finally {
      setScanning(false);
    }
  }

  useEffect(() => () => listener.current?.cancel(), []);

  const serverVoice = capabilities?.stt === "sarvam" || capabilities?.stt === "gemini";
  // "off": the patient said no to online AI help; the browser's own recogniser is online too.
  const voiceAvailable =
    question.input === "text" &&
    capabilities !== undefined &&
    capabilities.stt !== "off" &&
    ((serverVoice && recordingAvailable()) || browserRecognitionAvailable());

  const options = question.options ?? [];
  const toggleChip = (id: string) => {
    const option = options.find((item) => item.id === id);
    setChips((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (!question.multi || option?.none) return [id];
      // Picking a real symptom clears an earlier "none of these" and any choice that
      // contradicts it (e.g. "All the time" and "Comes and goes").
      const kept = current.filter((item) => {
        const other = options.find((candidate) => candidate.id === item);
        return (
          !other?.none &&
          !option?.excludes?.includes(item) &&
          !other?.excludes?.includes(id)
        );
      });
      return [...kept, id];
    });
  };

  const composed = [
    ...chips.map((id) => options.find((option) => option.id === id)?.label ?? ""),
    text.trim(),
  ]
    .filter(Boolean)
    .join(", ");
  const hasAnswer = question.input === "scale" ? scale !== undefined : composed.length > 0;

  function startListening() {
    stopSpeaking();
    setVoiceMessage("");
    setVoice("listening");
    const handlers = {
      onLevel: setLevel,
      onPartial: (partial: string) => {
        setShowText(true);
        setText(partial);
      },
      onProcessing: () => setVoice("processing"),
      onFinal: (heard: string) => {
        listener.current = null;
        setLevel(0);
        if (!heard) {
          setVoice("nothing");
          setVoiceMessage(t("voiceNothing"));
          return;
        }
        setShowText(true);
        setText((current) => {
          // Browser recognition streams partials into the box already.
          if (current && heard.startsWith(current.slice(0, 8))) return heard;
          return current && !current.includes(heard) ? `${current}, ${heard}` : heard;
        });
        setUsedVoice(true);
        setVoice("heard");
        setVoiceMessage(t("voiceHeard"));
      },
      onError: (code: "permission_denied" | "unsupported" | "failed") => {
        listener.current = null;
        setLevel(0);
        setVoice("error");
        setShowText(true);
        setVoiceMessage(t(code === "permission_denied" ? "voiceDenied" : "voiceFailed"));
      },
    };
    listener.current =
      serverVoice && recordingAvailable()
        ? listenWithServer((clip) => intake.transcribe(clip, question.id), handlers)
        : listenInBrowser(language, handlers);
  }

  const listening = voice === "listening";
  return (
    <div className="intake-answer">
      {question.input === "scale" ? (
        <div className="intake-scale" role="radiogroup" aria-label={question.prompt}>
          {Array.from({ length: 11 }, (_, value) => (
            <button
              type="button"
              role="radio"
              aria-checked={scale === value}
              key={value}
              className={`intake-scale-step tone-${Math.min(4, Math.floor(value / 2.5))} ${scale === value ? "is-selected" : ""}`}
              onClick={() => setScale(value)}
            >
              {value}
            </button>
          ))}
          <div className="intake-scale-legend">
            <span>0 · {t("scaleNone")}</span>
            <span>10 · {t("scaleWorst")}</span>
          </div>
        </div>
      ) : (
        <>
          {options.length > 0 && (
            <>
              <p className="intake-hint">{question.multi ? t("pickAll") : t("pickOne")}</p>
              <div className="intake-chips">
                {options.map((option) => {
                  const selected = chips.includes(option.id);
                  return (
                    <button
                      type="button"
                      key={option.id}
                      aria-pressed={selected}
                      className={`intake-chip ${selected ? "is-selected" : ""} ${option.none ? "is-none" : ""}`}
                      onClick={() => toggleChip(option.id)}
                    >
                      {selected && <Check aria-hidden="true" size={20} />}
                      {option.label}
                    </button>
                  );
                })}
              </div>
            </>
          )}
          <div className="intake-voice">
            {options.length > 0 && <p className="intake-hint">{t("orSpeak")}</p>}
            <div className="intake-voice-row">
              {voiceAvailable && (
                <button
                  type="button"
                  className={`intake-mic ${listening ? "is-listening" : ""}`}
                  disabled={busy || voice === "processing"}
                  onClick={() => (listening ? listener.current?.stop() : startListening())}
                  style={{ ["--level" as string]: Math.min(1, level * 12).toFixed(2) }}
                >
                  {voice === "processing" ? (
                    <Loader2 className="spin" aria-hidden="true" size={30} />
                  ) : listening ? (
                    <Square aria-hidden="true" size={28} />
                  ) : (
                    <Mic aria-hidden="true" size={32} />
                  )}
                  <span>
                    {voice === "processing"
                      ? t("understanding")
                      : listening
                        ? t("tapToStop")
                        : t("tapToSpeak")}
                  </span>
                </button>
              )}
              {!showText && (
                <button
                  type="button"
                  className="intake-type-toggle"
                  onClick={() => setShowText(true)}
                >
                  <Keyboard aria-hidden="true" size={20} />
                  {t("typeInstead")}
                </button>
              )}
              {canScan && (
                <>
                  <button
                    type="button"
                    className="intake-type-toggle"
                    disabled={scanning || busy}
                    onClick={() => photoInput.current?.click()}
                  >
                    {scanning ? (
                      <Loader2 className="spin" aria-hidden="true" size={20} />
                    ) : (
                      <Camera aria-hidden="true" size={20} />
                    )}
                    {t("scanPrescription")}
                  </button>
                  <input
                    ref={photoInput}
                    type="file"
                    accept="image/*"
                    capture="environment"
                    hidden
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (file) void scanPrescription(file);
                    }}
                  />
                </>
              )}
            </div>
            <p className="intake-voice-status" aria-live="polite">
              {listening ? t("listeningNow") : voiceMessage}
            </p>
            {!voiceAvailable && capabilities !== undefined && options.length === 0 && (
              <p className="intake-hint">
                {t(capabilities.stt === "off" ? "voiceOff" : "voiceUnavailable")}
              </p>
            )}
            {showText && (
              <textarea
                className="intake-textarea"
                lang={language}
                rows={3}
                maxLength={400}
                value={text}
                placeholder={t("typePlaceholder")}
                aria-label={t("typePlaceholder")}
                onChange={(event) => setText(event.target.value)}
              />
            )}
          </div>
        </>
      )}
      <div className="intake-actions">
        {canGoBack && (
          <button
            type="button"
            className="intake-btn intake-btn--ghost"
            disabled={busy || listening || voice === "processing"}
            onClick={onBack}
          >
            <ChevronLeft aria-hidden="true" size={22} />
            {t("back")}
          </button>
        )}
        <span className="intake-actions-spacer" />
        {question.optional && !editing && (
          <button
            type="button"
            className="intake-btn intake-btn--secondary"
            disabled={busy}
            onClick={() => onSubmit(null, "touch", true)}
          >
            {t("skip")}
          </button>
        )}
        <button
          type="button"
          className="intake-btn intake-btn--primary"
          disabled={busy || !hasAnswer || listening || voice === "processing"}
          onClick={() =>
            onSubmit(
              question.input === "scale" ? (scale ?? null) : composed.slice(0, 500),
              usedVoice ? "voice" : "touch",
              false,
            )
          }
        >
          {busy ? <Loader2 className="spin" aria-hidden="true" size={22} /> : null}
          {editing ? t("saveChange") : t("next")}
        </button>
      </div>
    </div>
  );
}

export function KioskIntake() {
  const { t } = useTranslation(["intake", "kiosk"]);
  const navigate = useNavigate();
  // On a patient's own phone (/m/<code>/intake) the start page is the check-in link.
  const { code: checkinCode } = useParams<{ code?: string }>();
  const mobile = Boolean(checkinCode);
  const home = mobile ? `/m/${checkinCode}` : "/kiosk";
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const hospitalName = useKioskHospitalName();
  const session = useMemo(() => intake.session(), []);
  const [consent, setConsent] = useState<ConsentCopy | undefined>(session?.consent);
  const [aiConsent, setAiConsent] = useState<ConsentCopy | undefined>(session?.ai_consent);
  const [smsConsent, setSmsConsent] = useState<ConsentCopy | undefined>(session?.sms_consent);
  const language: Language = consent?.language ?? "en";
  const [stage, setStage] = useState<Stage>((session?.stage as Stage) ?? "consent");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [capabilities, setCapabilities] = useState<SpeechCapabilities>();

  const [age, setAge] = useState("");
  const [name, setName] = useState("");
  const [sex, setSex] = useState<"female" | "male" | "other" | "">("");
  const [phone, setPhone] = useState("");
  // Queue messages only when the patient ticks the box themselves; never pre-ticked.
  const [smsOptIn, setSmsOptIn] = useState(false);
  // "Have you visited before?": ask, then the OPD card (number and PIN), then whether
  // today is about the same problem as the earlier visit.
  const [returningStep, setReturningStep] = useState<"ask" | "card" | "followup">("ask");
  const [cardNumber, setCardNumber] = useState("");
  const [cardPin, setCardPin] = useState("");
  const [cardError, setCardError] = useState("");
  const [earlierVisit, setEarlierVisit] = useState<{ id: string; date: string } | null>(null);

  const [tiles, setTiles] = useState<DangerOption[]>([]);
  const [selectedTiles, setSelectedTiles] = useState<string[]>([]);

  const [question, setQuestion] = useState<Question>();
  const [progress, setProgress] = useState<{ current: number; total: number }>();
  const [answered, setAnswered] = useState<Answered[]>([]);
  const [editing, setEditing] = useState<Answered>();
  // After "Back", the undone answer is shown pre-filled; ``turn`` remounts the answer panel.
  const [draft, setDraft] = useState<string | number | null>(null);
  const [turn, setTurn] = useState(0);
  const [consentGiven, setConsentGiven] = useState(stage !== "consent");
  const [quitOpen, setQuitOpen] = useState(false);
  const [emergency, setEmergency] = useState<"sending" | "sent" | "failed">();
  const [readback, setReadback] = useState("");
  const [redFlagged, setRedFlagged] = useState(false);
  const [nurseComing, setNurseComing] = useState(false);
  const [ticket, setTicket] = useState<QueueTicket>();
  const [secondsLeft, setSecondsLeft] = useState(RESTART_SECONDS);

  const move = useCallback((next: Stage) => {
    stopSpeaking();
    setStage(next);
    intake.setStage(next);
    setError("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const reportError = useCallback(
    (cause: unknown) => {
      // 410: the session ended; 401: the patient session expired (two hours).
      if (
        cause instanceof IntakeRequestError &&
        (cause.status === 410 || cause.status === 401)
      ) {
        setError(t("sessionLost"));
        return;
      }
      // The request never got an answer (Wi-Fi dropped): tapping again is safe, because the
      // server recognises an answer it has already saved.
      if (cause instanceof TypeError) {
        setError(t("connectionRetry"));
        return;
      }
      setError(cause instanceof Error && cause.message ? cause.message : t("errorRetry"));
    },
    [t],
  );

  const acceptInterviewResponse = useCallback(
    (result: IntakeResponse) => {
      setAnswered((result.answered ?? []) as Answered[]);
      setEditing(undefined);
      setDraft(result.draft ?? null);
      setTurn((value) => value + 1);
      if (result.complete) {
        setReadback(result.readback ?? "");
        setRedFlagged((result.case_sheet?.red_flags ?? []).length > 0);
        move("summary");
      } else {
        setQuestion(result.question);
        setProgress(result.progress);
        move("interview");
      }
    },
    [move],
  );

  useEffect(() => {
    if (!session || !consent) {
      navigate(home, { replace: true });
      return;
    }
    setLanguage(consent.language);
    void intake
      .capabilities()
      .then(setCapabilities)
      // Unknown: nothing goes online until the server confirms the patient's consent.
      .catch(() => setCapabilities({ stt: "off", tts: "browser", ai: false, online: false }));
    const resume = async () => {
      try {
        if (stage === "interview" || stage === "summary")
          acceptInterviewResponse(await intake.nextQuestion());
        if (stage === "danger") setTiles((await intake.dangerTiles()).tiles);
      } catch (cause) {
        reportError(cause);
      }
    };
    void resume();
    // The stage is read once on mount to resume an interrupted kiosk flow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // A kiosk patient idle on the review screen gets a nurse; a phone user could be anywhere
    // (and may simply have switched apps), so phones use the Help button instead.
    if (stage !== "summary" || nurseComing || mobile) return;
    let timer = 0;
    const arm = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        setNurseComing(true);
        void intake.needsAssistance().catch(() => undefined);
      }, SUMMARY_IDLE_MS);
    };
    arm();
    window.addEventListener("pointerdown", arm);
    window.addEventListener("keydown", arm);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("pointerdown", arm);
      window.removeEventListener("keydown", arm);
    };
  }, [stage, nurseComing, mobile]);

  useEffect(() => {
    // The shared kiosk frees itself for the next patient; a phone keeps showing the token.
    if (stage !== "done" || mobile) return;
    const timer = window.setInterval(() => {
      setSecondsLeft((value) => {
        if (value <= 1) {
          window.clearInterval(timer);
          navigate("/kiosk", { replace: true });
          return 0;
        }
        return value - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [stage, navigate, mobile]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      reportError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function toSafetyScreen() {
    setTiles((await intake.dangerTiles()).tiles);
    move("danger");
  }

  /** Record the answer about online AI help; voice and photo reading follow it. */
  async function answerAi(granted: boolean) {
    if (!aiConsent) return;
    await run(async () => {
      await intake.aiConsent(aiConsent, granted);
      void intake
        .capabilities()
        .then(setCapabilities)
        .catch(() => undefined);
      move("identity");
    });
  }

  /** Save the details, then ask a patient at a kiosk or phone whether they came before. */
  async function submitIdentity() {
    await intake.identity(
      Number(age),
      sex || "unknown",
      phone || undefined,
      session?.assisted ? name.trim() : undefined,
      smsOptIn ? smsConsent : undefined,
    );
    // At the desk the nurse finds an earlier record from the consultation screen.
    if (session?.assisted) {
      await toSafetyScreen();
      return;
    }
    setReturningStep("ask");
    move("returning");
  }

  /** Join today's visit to the earlier record; the kiosk never shows that record. */
  async function linkCard() {
    setCardError("");
    try {
      const link = await intake.linkOpdCard(cardNumber.trim(), cardPin.trim());
      if (link.previous_visit) {
        setEarlierVisit(link.previous_visit);
        setReturningStep("followup");
        return;
      }
    } catch (cause) {
      // The patient can fix the card details or carry on as a new visit.
      if (cause instanceof IntakeRequestError && cause.status < 500) {
        setCardError(cause.message);
        return;
      }
      throw cause;
    }
    await toSafetyScreen();
  }

  /** End the visit without a token: it is cancelled and kept off the staff patient list. */
  async function quit() {
    setQuitOpen(false);
    stopSpeaking();
    setBusy(true);
    await intake.cancel();
    navigate(session?.assisted ? "/staff/queue" : home, { replace: true });
  }

  /** Back inside the interview: undo the last answer, or return to the safety screen. */
  async function goBack() {
    if (editing) {
      setEditing(undefined);
      if (!question) move("summary");
      return;
    }
    await run(async () => {
      if (answered.length === 0) {
        setTiles((await intake.dangerTiles()).tiles);
        move("danger");
        return;
      }
      acceptInterviewResponse(await intake.back());
    });
  }

  async function switchLanguage(next: Language) {
    if (next === language || busy) return;
    stopSpeaking();
    await run(async () => {
      const result = await intake.changeLanguage(next);
      setConsent(result.consent);
      setAiConsent(result.ai_consent);
      setSmsConsent(result.sms_consent);
      // The notice changed language: the patient ticks again if they still want SMS.
      setSmsOptIn(false);
      setLanguage(next);
      if (stage === "danger") setTiles((await intake.dangerTiles()).tiles);
      if (stage === "interview" || stage === "summary") {
        const response = await intake.nextQuestion();
        const answeredNow = (response.answered ?? []) as Answered[];
        setAnswered(answeredNow);
        if (response.complete) setReadback(response.readback ?? "");
        else {
          setQuestion(response.question);
          setProgress(response.progress);
        }
        if (editing) setEditing(answeredNow.find((item) => item.id === editing.id) ?? editing);
      }
    });
  }

  if (!session || !consent) return null;

  const phoneValid = phone === "" || /^[6-9]\d{9}$/.test(phone);
  const ageValid = age !== "" && Number(age) >= 0 && Number(age) <= 120;
  const currentQuestion = editing ?? question;

  return (
    <main className="kiosk-main intake" aria-busy={busy}>
      {stage !== "done" && stage !== "help" && stage !== "emergency" && (
        <LanguageSwitch
          value={language}
          disabled={busy}
          onChange={(next) => void switchLanguage(next)}
          leading={
            <button
              type="button"
              className="intake-quit"
              disabled={busy}
              onClick={() => {
                stopSpeaking();
                setQuitOpen(true);
              }}
            >
              <LogOut aria-hidden="true" size={20} />
              {t("quit")}
            </button>
          }
        />
      )}
      <ConfirmDialog
        open={quitOpen}
        title={t("quitTitle")}
        confirmLabel={t("quitYes")}
        cancelLabel={t("quitNo")}
        onConfirm={() => void quit()}
        onCancel={() => setQuitOpen(false)}
      >
        <p>{t("quitBody")}</p>
      </ConfirmDialog>
      {stage !== "emergency" && <Stepper stage={stage} />}
      <section className="intake-card" lang={language}>
        {error && (
          <div className="intake-error" role="alert">
            <AlertTriangle aria-hidden="true" size={22} />
            <span>{error}</span>
            {error === t("sessionLost") && (
              <button
                type="button"
                onClick={() => {
                  intake.finish();
                  navigate(home, { replace: true });
                }}
              >
                {t("startAgain")}
              </button>
            )}
          </div>
        )}

        {stage === "consent" && (
          <>
            <h1 className="intake-title">{t("consentTitle")}</h1>
            <p className="intake-lead">{consent.text}</p>
            <SpeakButton
              text={consent.text}
              language={language}
              autoPlayKey={`consent-${language}`}
              fetchAudio={
                capabilities?.tts && capabilities.tts !== "browser"
                  ? () => intake.speechAudio("consent")
                  : undefined
              }
            />
            <div className="intake-actions intake-actions--stack">
              <button
                type="button"
                className="intake-btn intake-btn--primary intake-btn--large"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await intake.consent(consent, true);
                    setConsentGiven(true);
                    // Online AI help is a separate question with its own notice.
                    move(aiConsent ? "ai" : "identity");
                  })
                }
              >
                <Check aria-hidden="true" size={24} />
                {t("consentAgree")}
              </button>
              <button
                type="button"
                className="intake-btn intake-btn--ghost"
                disabled={busy}
                onClick={() => {
                  // Already agreed and came back here: declining now means stopping.
                  if (consentGiven) {
                    setQuitOpen(true);
                    return;
                  }
                  void run(async () => {
                    await intake.consent(consent, false);
                    intake.finish();
                    move("help");
                  });
                }}
              >
                {t("consentDecline")}
              </button>
            </div>
          </>
        )}

        {stage === "ai" && aiConsent && (
          <>
            <h1 className="intake-title">{t("aiTitle")}</h1>
            <p className="intake-lead">{aiConsent.text}</p>
            <SpeakButton
              text={aiConsent.text}
              language={language}
              autoPlayKey={`consent-ai-${language}`}
              fetchAudio={
                capabilities?.tts && capabilities.tts !== "browser"
                  ? () => intake.speechAudio("consent_ai")
                  : undefined
              }
            />
            {/* Two equal choices, styled alike so neither is nudged: nothing is pre-selected
                and either one continues. */}
            <div className="intake-actions intake-actions--stack">
              <button
                type="button"
                className="intake-btn intake-btn--secondary intake-btn--large"
                disabled={busy}
                onClick={() => void answerAi(true)}
              >
                {t("aiYes")}
              </button>
              <button
                type="button"
                className="intake-btn intake-btn--secondary intake-btn--large"
                disabled={busy}
                onClick={() => void answerAi(false)}
              >
                {t("aiNo")}
              </button>
            </div>
          </>
        )}

        {stage === "identity" && (
          <>
            <h1 className="intake-title">{t("detailsTitle")}</h1>
            {session.assisted && (
              <label className="intake-field">
                <span>{t("nameQuestion")}</span>
                <input
                  autoComplete="name"
                  maxLength={200}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
            )}
            <div className="intake-field">
              <span id="age-label">{t("ageQuestion")}</span>
              <div className="intake-age">
                <input
                  id="intake-age"
                  aria-labelledby="age-label"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={120}
                  value={age}
                  onChange={(event) => setAge(event.target.value.slice(0, 3))}
                />
                <span>{t("ageUnit")}</span>
              </div>
            </div>
            <div className="intake-field">
              <span id="sex-label">{t("sexQuestion")}</span>
              <div className="intake-segmented" role="radiogroup" aria-labelledby="sex-label">
                {(["female", "male", "other"] as const).map((value) => (
                  <button
                    type="button"
                    role="radio"
                    aria-checked={sex === value}
                    key={value}
                    className={sex === value ? "is-selected" : ""}
                    onClick={() => setSex(value)}
                  >
                    {t(value === "female" ? "sexFemale" : value === "male" ? "sexMale" : "sexOther")}
                  </button>
                ))}
              </div>
            </div>
            <label className="intake-field">
              <span>{t("phoneQuestion")}</span>
              <small>{t("phoneHint")}</small>
              <input
                id="intake-phone"
                inputMode="numeric"
                autoComplete="tel-national"
                maxLength={10}
                value={phone}
                onChange={(event) => {
                  const digits = event.target.value.replace(/\D/g, "");
                  setPhone(digits);
                  // A changed number needs a fresh tick: consent is for one number.
                  if (digits !== phone) setSmsOptIn(false);
                  setError("");
                }}
              />
              {!phoneValid && phone.length === 10 && (
                <small className="intake-field-error">{t("phoneInvalid")}</small>
              )}
            </label>
            {phone.length === 10 && phoneValid && smsConsent && (
              <label className="intake-check">
                <input
                  type="checkbox"
                  checked={smsOptIn}
                  onChange={(event) => setSmsOptIn(event.target.checked)}
                />
                <span>{smsConsent.text}</span>
              </label>
            )}
            <div className="intake-actions">
              <button
                type="button"
                className="intake-btn intake-btn--ghost"
                disabled={busy}
                onClick={() => move(aiConsent ? "ai" : "consent")}
              >
                <ChevronLeft aria-hidden="true" size={22} />
                {t("back")}
              </button>
              <span className="intake-actions-spacer" />
              <button
                type="button"
                className="intake-btn intake-btn--primary"
                disabled={
                  busy ||
                  !ageValid ||
                  !sex ||
                  !phoneValid ||
                  (session.assisted === true && !name.trim())
                }
                onClick={() => void run(submitIdentity)}
              >
                {t("next")}
              </button>
            </div>
          </>
        )}

        {stage === "returning" && returningStep === "ask" && (
          <>
            <h1 className="intake-title">{t("returningTitle")}</h1>
            <p className="intake-lead">{t("returningLead")}</p>
            <div className="intake-actions intake-actions--stack">
              <button
                type="button"
                className="intake-btn intake-btn--primary intake-btn--large"
                disabled={busy}
                onClick={() => setReturningStep("card")}
              >
                <IdCard aria-hidden="true" size={24} />
                {t("returningYes")}
              </button>
              <button
                type="button"
                className="intake-btn intake-btn--secondary intake-btn--large"
                disabled={busy}
                onClick={() => void run(toSafetyScreen)}
              >
                {t("returningNo")}
              </button>
              <button
                type="button"
                className="intake-btn intake-btn--ghost"
                disabled={busy}
                onClick={() => move("identity")}
              >
                <ChevronLeft aria-hidden="true" size={22} />
                {t("back")}
              </button>
            </div>
          </>
        )}

        {stage === "returning" && returningStep === "card" && (
          <>
            <h1 className="intake-title">{t("opdCardTitle")}</h1>
            <div className="intake-opd-card-fields">
              <label className="intake-field">
                <span>{t("opdCardNumber")}</span>
                <input
                  inputMode="numeric"
                  autoComplete="off"
                  maxLength={14}
                  value={cardNumber}
                  onChange={(event) => {
                    setCardNumber(event.target.value.replace(/[^\d -]/g, ""));
                    setCardError("");
                  }}
                />
              </label>
              <label className="intake-field">
                <span>{t("opdCardPin")}</span>
                <input
                  inputMode="numeric"
                  autoComplete="off"
                  maxLength={6}
                  value={cardPin}
                  onChange={(event) => {
                    setCardPin(event.target.value.replace(/\D/g, ""));
                    setCardError("");
                  }}
                />
              </label>
            </div>
            <p className="intake-hint">{t("opdCardHint")}</p>
            {cardError && (
              <p className="intake-staff-note" role="alert">
                {cardError}
              </p>
            )}
            <div className="intake-actions">
              <button
                type="button"
                className="intake-btn intake-btn--ghost"
                disabled={busy}
                onClick={() => setReturningStep("ask")}
              >
                <ChevronLeft aria-hidden="true" size={22} />
                {t("back")}
              </button>
              <span className="intake-actions-spacer" />
              {cardError && (
                <button
                  type="button"
                  className="intake-btn intake-btn--secondary"
                  disabled={busy}
                  onClick={() => void run(toSafetyScreen)}
                >
                  {t("continueWithoutCard")}
                </button>
              )}
              <button
                type="button"
                className="intake-btn intake-btn--primary"
                disabled={busy || cardNumber.trim().length < 8 || cardPin.length !== 6}
                onClick={() => void run(linkCard)}
              >
                {t("next")}
              </button>
            </div>
          </>
        )}

        {stage === "returning" && returningStep === "followup" && earlierVisit && (
          <>
            <h1 className="intake-title">
              {t("followUpTitle", {
                date: new Date(`${earlierVisit.date}T00:00:00`).toLocaleDateString(
                  language === "en" ? "en-IN" : `${language}-IN`,
                  { day: "numeric", month: "long" },
                ),
              })}
            </h1>
            <p className="intake-lead">{t("followUpLead")}</p>
            <div className="intake-actions intake-actions--stack">
              <button
                type="button"
                className="intake-btn intake-btn--primary intake-btn--large"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await intake.markFollowUp(earlierVisit.id);
                    await toSafetyScreen();
                  })
                }
              >
                {t("followUpSame")}
              </button>
              <button
                type="button"
                className="intake-btn intake-btn--secondary intake-btn--large"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await intake.markFollowUp(null);
                    await toSafetyScreen();
                  })
                }
              >
                {t("followUpNew")}
              </button>
            </div>
          </>
        )}

        {stage === "danger" && (
          <>
            <h1 className="intake-title">{t("dangerTitle")}</h1>
            <p className="intake-lead">{t("dangerSubtitle")}</p>
            <div className="intake-danger-grid">
              {tiles.map((tile) => {
                const selected = selectedTiles.includes(tile.id);
                return (
                  <button
                    type="button"
                    key={tile.id}
                    aria-pressed={selected}
                    className={`intake-danger ${selected ? "is-selected" : ""}`}
                    onClick={() =>
                      setSelectedTiles((current) =>
                        current.includes(tile.id)
                          ? current.filter((item) => item !== tile.id)
                          : [...current, tile.id],
                      )
                    }
                  >
                    <AlertTriangle aria-hidden="true" size={26} />
                    <span>{tile.label}</span>
                    {selected && <Check className="intake-danger-check" aria-hidden="true" />}
                  </button>
                );
              })}
            </div>
            {selectedTiles.length > 0 && (
              <p className="intake-staff-note" role="status">
                {t("dangerAlert")}
              </p>
            )}
            <div className="intake-actions">
              <button
                type="button"
                className="intake-btn intake-btn--ghost"
                disabled={busy}
                onClick={() => move("identity")}
              >
                <ChevronLeft aria-hidden="true" size={22} />
                {t("back")}
              </button>
              <span className="intake-actions-spacer" />
              {selectedTiles.length === 0 ? (
                <button
                  type="button"
                  className="intake-btn intake-btn--primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await intake.dangerSigns([]);
                      acceptInterviewResponse(await intake.nextQuestion());
                    })
                  }
                >
                  {t("dangerNone")}
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="intake-btn intake-btn--secondary"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await intake.dangerSigns(selectedTiles);
                        acceptInterviewResponse(await intake.nextQuestion());
                      })
                    }
                  >
                    {t("dangerContinue")}
                  </button>
                  {/* A danger sign should not wait behind a questionnaire. */}
                  <button
                    type="button"
                    className="intake-btn intake-btn--danger"
                    disabled={busy}
                    onClick={() => {
                      setEmergency("sending");
                      move("emergency");
                      void intake
                        .dangerSigns(selectedTiles)
                        .then(() => setEmergency("sent"))
                        .catch(() => setEmergency("failed"))
                        // The visit stays open with its staff alert; the kiosk lets go of it
                        // so the idle reset does not cancel it.
                        .finally(() => intake.finish());
                    }}
                  >
                    {t("dangerHelpNow")}
                  </button>
                </>
              )}
            </div>
          </>
        )}

        {stage === "interview" && currentQuestion && (
          <>
            {mobile && !secureVoiceAvailable() && progress?.current === 1 && (
              <p className="mobile-note">{t("mobileVoiceNote", { ns: "kiosk" })}</p>
            )}
            {progress && !editing && (
              <div className="intake-progress">
                <span>{t("questionOf", progress)}</span>
                <div className="intake-progress-bar" aria-hidden="true">
                  <div
                    style={{
                      width: `${Math.min(100, (progress.current / Math.max(progress.total, 1)) * 100)}%`,
                    }}
                  />
                </div>
              </div>
            )}
            <div className="intake-question-head">
              <h1 className="intake-title">{currentQuestion.prompt}</h1>
              <SpeakButton
                text={currentQuestion.prompt}
                language={language}
                autoPlayKey={`q-${currentQuestion.id}-${editing ? "edit" : "ask"}-${language}`}
                fetchAudio={
                  capabilities?.tts && capabilities.tts !== "browser"
                    ? () => intake.speechAudio("question", currentQuestion.id)
                    : undefined
                }
              />
            </div>
            <AnswerPanel
              key={`${currentQuestion.id}-${editing ? "edit" : `ask-${turn}`}`}
              question={currentQuestion}
              language={language}
              capabilities={capabilities}
              initial={editing ? editing.answer : draft}
              busy={busy}
              editing={Boolean(editing)}
              canGoBack
              onBack={() => void goBack()}
              onSubmit={(answer, mode, skip) =>
                void run(async () => {
                  const response = editing
                    ? await intake.correct(editing.id, answer ?? "", mode)
                    : await intake.answer(currentQuestion.id, answer, skip, mode);
                  acceptInterviewResponse(response);
                })
              }
            />
          </>
        )}

        {stage === "summary" && (
          <>
            <h1 className="intake-title">{t("reviewTitle")}</h1>
            <p className="intake-lead">{t("reviewSubtitle")}</p>
            {redFlagged && (
              <p className="intake-staff-note" role="alert">
                {t("dangerAlert")}
              </p>
            )}
            {nurseComing && (
              <p className="intake-staff-note" role="status">
                {t("needsAssistance", { ns: "kiosk" })}
              </p>
            )}
            {readback && (
              <div className="intake-readback">
                <p>{readback}</p>
                <SpeakButton
                  text={readback}
                  language={language}
                  autoPlayKey={`readback-${language}`}
                  // The read-back is the patient's own words: an online voice only with
                  // their consent to online AI help, otherwise a voice on this device.
                  localOnly={!capabilities?.online}
                  fetchAudio={
                    capabilities?.online && capabilities.tts !== "browser"
                      ? () => intake.speechAudio("readback")
                      : undefined
                  }
                />
              </div>
            )}
            <ul className="intake-review">
              {answered.map((item) => (
                <li key={item.id}>
                  <div>
                    <span className="intake-review-q">{item.prompt}</span>
                    <strong className="intake-review-a">
                      {item.answer === null || item.answer === ""
                        ? t("skipped")
                        : typeof item.answer === "number"
                          ? `${item.answer} / 10`
                          : item.answer}
                    </strong>
                  </div>
                  {item.answer !== null && item.answer !== "" && (
                    <button
                      type="button"
                      className="intake-btn intake-btn--ghost intake-btn--small"
                      aria-label={`${t("change")}: ${item.prompt}`}
                      onClick={() => {
                        setQuestion(undefined);
                        setEditing(item);
                        move("interview");
                      }}
                    >
                      {t("change")}
                    </button>
                  )}
                </li>
              ))}
            </ul>
            <KioskPapers online={capabilities?.online === true} disabled={busy} />
            <div className="intake-actions">
              <span className="intake-actions-spacer" />
              <button
                type="button"
                className="intake-btn intake-btn--primary intake-btn--large"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const result = (await intake.confirm()) as Awaited<
                      ReturnType<typeof intake.confirm>
                    > & {
                      department_name?: string | null;
                      wait_minutes?: number | null;
                      public_status_url?: string | null;
                    };
                    setTicket({
                      token: result.token,
                      statusUrl: result.status_url,
                      publicStatusUrl: result.public_status_url ?? null,
                      issuedAt: new Date(),
                      position: result.position,
                      departmentName: result.department_name ?? null,
                      waitMinutes: result.wait_minutes ?? null,
                      opdCard: result.opd_card ?? null,
                    });
                    if (mobile)
                      intake.rememberTicket({
                        token: result.token,
                        statusUrl: result.status_url,
                        departmentName: result.department_name ?? null,
                        hospitalName,
                        issuedAt: new Date().toISOString(),
                        opdCard: result.opd_card ?? null,
                      });
                    intake.finish();
                    setSecondsLeft(RESTART_SECONDS);
                    move("done");
                  })
                }
              >
                <Check aria-hidden="true" size={24} />
                {t("confirm")}
              </button>
            </div>
          </>
        )}

        {stage === "done" && ticket && (
          <div className="intake-ticket">
            <div className="intake-ticket-ok">
              <Check aria-hidden="true" size={36} />
            </div>
            <h1 className="intake-title">{t("doneTitle")}</h1>
            <div className="intake-ticket-main">
              <div className="intake-ticket-token">
                <span>{t("yourToken")}</span>
                <strong className="status-token">{ticket.token}</strong>
                {ticket.departmentName && (
                  <p>
                    {t("department")}: <b>{ticket.departmentName}</b>
                  </p>
                )}
                {ticket.position !== null && (
                  <p className="intake-ticket-position">
                    {ticket.position <= 1
                      ? t("aheadNone")
                      : t("ahead", { count: ticket.position - 1 })}
                    {ticket.waitMinutes ? ` · ${t("waitAbout", { minutes: ticket.waitMinutes })}` : ""}
                  </p>
                )}
              </div>
              {/* Not a link: tapping it would take the shared kiosk away from the next patient.
                  A phone is already in the patient's hand, so it needs no QR. */}
              {!mobile && (
                <div className="intake-ticket-qr">
                  <QRCodeSVG
                    value={
                      ticket.publicStatusUrl ??
                      new URL(ticket.statusUrl, window.location.origin).toString()
                    }
                    size={184}
                    level="M"
                    marginSize={2}
                    title={t("scanQr")}
                  />
                  <span>{t("scanQr")}</span>
                </div>
              )}
            </div>
            {ticket.opdCard && (
              <div className="intake-opd-slip">
                <span>
                  <IdCard aria-hidden="true" size={18} /> {t("yourOpdCard")}
                </span>
                <strong>
                  {t("opdCardLine", { number: ticket.opdCard.number, pin: ticket.opdCard.pin })}
                </strong>
                <small>{t("opdCardKeep")}</small>
              </div>
            )}
            <p className="intake-lead">{t("waitHint")}</p>
            {mobile && <p className="mobile-note">{t("mobileKeepPage", { ns: "kiosk" })}</p>}
            <p className="intake-ticket-meta print-only">
              {hospitalName ? `${hospitalName} · ` : ""}
              {ticket.issuedAt.toLocaleString(language === "en" ? "en-IN" : `${language}-IN`, {
                dateStyle: "medium",
                timeStyle: "short",
              })}
            </p>
            {mobile ? (
              <div className="intake-actions intake-actions--stack no-print">
                <button
                  type="button"
                  className="intake-btn intake-btn--primary intake-btn--large"
                  onClick={() => navigate(ticket.statusUrl, { replace: true })}
                >
                  {t("mobileViewStatus", { ns: "kiosk" })}
                </button>
                <button
                  type="button"
                  className="intake-btn intake-btn--ghost"
                  onClick={() => navigate(home, { replace: true })}
                >
                  {t("mobileSomeoneElse", { ns: "kiosk" })}
                </button>
              </div>
            ) : (
              <>
                <div className="intake-actions no-print">
                  <button
                    type="button"
                    className="intake-btn intake-btn--secondary"
                    onClick={() => window.print()}
                  >
                    {t("print")}
                  </button>
                  <span className="intake-actions-spacer" />
                  <button
                    type="button"
                    className="intake-btn intake-btn--primary"
                    onClick={() => navigate("/kiosk", { replace: true })}
                  >
                    {t("newPatient")}
                  </button>
                </div>
                <p className="intake-restart no-print">
                  {t("autoRestart", { seconds: secondsLeft })}
                </p>
              </>
            )}
          </div>
        )}

        {stage === "emergency" && emergency && (
          <EmergencyNotice
            state={emergency}
            mobile={mobile}
            onDone={() => navigate(home, { replace: true })}
          />
        )}

        {stage === "help" && (
          <>
            <h1 className="intake-title">{t("staffMessage", { ns: "kiosk" })}</h1>
            <div className="intake-actions">
              <button
                type="button"
                className="intake-btn intake-btn--primary"
                onClick={() => navigate(home, { replace: true })}
              >
                {t("startAgain")}
              </button>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
