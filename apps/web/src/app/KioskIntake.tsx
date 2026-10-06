import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { DangerTile, FacesScale, VoiceOrb } from "../components/ui";
import {
  intake,
  type CaseSheet,
  type ConsentCopy,
  type DangerTile as DangerOption,
  type IntakeResponse,
  type Question,
} from "../lib/intake";
import { useLanguageStore } from "../languageStore";
import { QRCodeSVG } from "qrcode.react";
import { AudioPrompt, ReadBackAudioButton } from "../components/AudioPrompt";
import {
  CaptureError,
  startMicrophoneCapture,
  type StopCapture,
} from "../lib/audioCapture";

type Stage =
  "consent" | "identity" | "danger" | "interview" | "summary" | "done" | "help";
type VoiceState =
  "idle" | "connecting" | "recording" | "processing" | "review" | "fallback";

function languageFrom(copy?: ConsentCopy) {
  return copy?.language ?? "en";
}

export function KioskIntake() {
  const { t } = useTranslation(["common", "kiosk"]);
  const navigate = useNavigate();
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const session = useMemo(() => intake.session(), []);
  const consent = session?.consent;
  const [stage, setStage] = useState<Stage>(
    (session?.stage as Stage) ?? "consent",
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [age, setAge] = useState("");
  const [name, setName] = useState("");
  const [sex, setSex] = useState("unknown");
  const [phone, setPhone] = useState("");
  const [smsOptIn, setSmsOptIn] = useState(false);
  const [tiles, setTiles] = useState<DangerOption[]>([]);
  const [selectedTiles, setSelectedTiles] = useState<string[]>([]);
  const [question, setQuestion] = useState<Question>();
  const [answered, setAnswered] = useState<
    (Question & { answer: string | number })[]
  >([]);
  const [editing, setEditing] = useState<Question>();
  const [answer, setAnswer] = useState<string | number>("");
  const [answerMode, setAnswerMode] = useState<"touch" | "voice">("touch");
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [voiceLevel, setVoiceLevel] = useState(0);
  const [voiceText, setVoiceText] = useState("");
  const [voiceError, setVoiceError] = useState("");
  const voiceSocket = useRef<WebSocket | null>(null);
  const stopCapture = useRef<StopCapture | null>(null);
  const hasVoiceTranscript = useRef(false);
  const voiceFallbackTimer = useRef<number | undefined>(undefined);
  const summaryIdleTimer = useRef<number | undefined>(undefined);
  const [caseSheet, setCaseSheet] = useState<CaseSheet>();
  const [readback, setReadback] = useState("");
  const [needsAssistance, setNeedsAssistance] = useState(false);
  const [queueToken, setQueueToken] = useState<{
    token: string;
    statusUrl: string;
  }>();

  useEffect(() => {
    if (!session || !consent) {
      navigate("/kiosk", { replace: true });
      return;
    }
    setLanguage(languageFrom(consent));
    const resume = async () => {
      if (stage === "interview" || stage === "summary") {
        try {
          const result = await intake.nextQuestion();
          acceptInterviewResponse(result);
        } catch (cause) {
          reportError(cause);
        }
      }
      if (stage === "danger") {
        try {
          setTiles((await intake.dangerTiles()).tiles);
        } catch (cause) {
          reportError(cause);
        }
      }
    };
    void resume();
    // The stage is deliberately read only on mount to resume this kiosk flow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function reportError(cause: unknown) {
    setError(
      cause instanceof Error ? cause.message : t("errorTitle", { ns: "kiosk" }),
    );
  }

  function move(next: Stage) {
    setStage(next);
    intake.setStage(next);
    setError("");
  }

  function acceptInterviewResponse(result: IntakeResponse) {
    setAnswered(result.answered ?? []);
    if (result.complete) {
      setCaseSheet(result.case_sheet);
      setReadback(result.readback ?? "");
      move("summary");
    } else {
      setQuestion(result.question);
      setAnswer("");
      setEditing(undefined);
      move("interview");
    }
  }

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

  const currentQuestion = editing ?? question;

  useEffect(
    () => () => {
      if (voiceFallbackTimer.current !== undefined)
        window.clearTimeout(voiceFallbackTimer.current);
      void stopCapture.current?.();
      voiceSocket.current?.close();
    },
    [],
  );

  useEffect(() => {
    if (stage !== "summary" || needsAssistance) return;
    const schedule = () => {
      if (summaryIdleTimer.current !== undefined)
        window.clearTimeout(summaryIdleTimer.current);
      summaryIdleTimer.current = window.setTimeout(() => {
        void intake
          .needsAssistance()
          .then(() => setNeedsAssistance(true))
          .catch(() => setNeedsAssistance(true));
      }, 90_000);
    };
    const resetOnActivity = () => schedule();
    schedule();
    window.addEventListener("pointerdown", resetOnActivity);
    window.addEventListener("keydown", resetOnActivity);
    return () => {
      if (summaryIdleTimer.current !== undefined)
        window.clearTimeout(summaryIdleTimer.current);
      window.removeEventListener("pointerdown", resetOnActivity);
      window.removeEventListener("keydown", resetOnActivity);
    };
  }, [stage, needsAssistance]);

  function clearVoiceFallbackTimer() {
    if (voiceFallbackTimer.current !== undefined) {
      window.clearTimeout(voiceFallbackTimer.current);
      voiceFallbackTimer.current = undefined;
    }
  }

  function fallBackToTouch() {
    clearVoiceFallbackTimer();
    const stop = stopCapture.current;
    stopCapture.current = null;
    void stop?.();
    voiceSocket.current?.close();
    voiceSocket.current = null;
    setVoiceLevel(0);
    setVoiceError(t("voiceFallback", { ns: "kiosk" }));
    setVoiceState("fallback");
  }

  function scheduleVoiceFallback() {
    clearVoiceFallbackTimer();
    voiceFallbackTimer.current = window.setTimeout(fallBackToTouch, 3_000);
  }

  async function startVoice() {
    if (!consent || !currentQuestion || voiceState === "connecting") return;
    setVoiceError("");
    setVoiceText("");
    hasVoiceTranscript.current = false;
    clearVoiceFallbackTimer();
    setVoiceState("connecting");
    let media: MediaStream | undefined;
    let socket: WebSocket | undefined;
    let releaseReady: (() => void) | undefined;
    let rejectReady: ((cause: Error) => void) | undefined;
    let readyResolved = false;
    try {
      if (!navigator.mediaDevices?.getUserMedia)
        throw new CaptureError("unsupported");
      // Request permission directly from the patient's button tap.
      media = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const ready = new Promise<void>((resolve, reject) => {
        releaseReady = () => {
          readyResolved = true;
          resolve();
        };
        rejectReady = reject;
      });
      socket = intake.connectAudio((event) => {
        let packet: {
          type?: string;
          text?: string;
          confidence?: number;
        };
        try {
          packet = JSON.parse(event.data) as typeof packet;
        } catch {
          packet = { type: "error" };
        }
        if (packet.type === "ready") {
          releaseReady?.();
          return;
        }
        if (packet.type === "error" || packet.type === "fallback") {
          if (!readyResolved) {
            rejectReady?.(new Error("connection_failed"));
          } else {
            fallBackToTouch();
          }
          return;
        }
        if (packet.type === "partial" || packet.type === "final") {
          hasVoiceTranscript.current = true;
          if (packet.type === "partial") scheduleVoiceFallback();
          else clearVoiceFallbackTimer();
          const transcript = packet.text ?? "";
          setVoiceText(transcript);
          setAnswer(transcript);
          setAnswerMode("voice");
          if (packet.type === "final") {
            void stopCapture.current?.();
            stopCapture.current = null;
            socket?.close();
            voiceSocket.current = null;
            setVoiceLevel(0);
            setVoiceState(
              packet.confidence === undefined || packet.confidence < 0.65
                ? "review"
                : "idle",
            );
          }
        }
      });
      voiceSocket.current = socket;
      socket.addEventListener("close", () => {
        if (!readyResolved) rejectReady?.(new Error("connection_failed"));
      });
      socket.addEventListener("error", () => {
        if (!readyResolved) rejectReady?.(new Error("connection_failed"));
      });
      let readyTimer: number | undefined;
      try {
        await Promise.race([
          ready,
          new Promise<never>((_, reject) => {
            readyTimer = window.setTimeout(
              () => reject(new Error("connection_failed")),
              10_000,
            );
          }),
        ]);
      } finally {
        if (readyTimer !== undefined) window.clearTimeout(readyTimer);
      }
      socket.send(
        JSON.stringify({
          type: "start",
          lang: consent.language,
          question_id: currentQuestion.id,
        }),
      );
      stopCapture.current = await startMicrophoneCapture(socket, {
        stream: media,
        onLevel: setVoiceLevel,
        onSpeech: scheduleVoiceFallback,
        onStop: (reason) => {
          if (reason === "silence" && !hasVoiceTranscript.current)
            fallBackToTouch();
          else
            setVoiceState((current) =>
              current === "recording" || current === "connecting"
                ? "processing"
                : current,
            );
        },
      });
      media = undefined;
      setVoiceState("recording");
    } catch (cause) {
      clearVoiceFallbackTimer();
      media?.getTracks().forEach((track) => track.stop());
      socket?.close();
      voiceSocket.current = null;
      const code =
        cause instanceof CaptureError
          ? cause.code
          : cause instanceof DOMException && cause.name === "NotAllowedError"
            ? "permission_denied"
            : cause instanceof DOMException && cause.name === "NotFoundError"
              ? "unsupported"
              : "connection_failed";
      setVoiceError(
        code === "permission_denied"
          ? t("voicePermissionDenied", { ns: "kiosk" })
          : code === "unsupported"
            ? t("voiceUnsupported", { ns: "kiosk" })
            : t("voiceFallback", { ns: "kiosk" }),
      );
      setVoiceState("fallback");
    }
  }

  const onPrevious = () => {
    const previous = answered.at(-1);
    if (!previous) return;
    setEditing(previous);
    setAnswer(previous.answer);
  };

  const submitAnswer = async (skip = false) => {
    if (!currentQuestion) return;
    await run(async () => {
      const response = editing
        ? await intake.correct(editing.id, answer, answerMode)
        : await intake.answer(
            currentQuestion.id,
            skip ? null : answer,
            skip,
            answerMode,
          );
      acceptInterviewResponse(response);
      setAnswerMode("touch");
      setVoiceState("idle");
    });
  };

  if (!session || !consent) return null;

  return (
    <main className="kiosk-main kiosk-intake" aria-busy={busy}>
      <section className="welcome-card kiosk-intake-card">
        {error && (
          <div className="ui-toast ui-toast--error" role="alert">
            {error}
          </div>
        )}
        {stage === "consent" && (
          <>
            <p className="eyebrow">{t("screenS2", { ns: "kiosk" })}</p>
            <h1>{t("consentContinue", { ns: "kiosk" })}</h1>
            <p lang={consent.language}>{consent.text}</p>
            <AudioPrompt
              id="consent"
              language={consent.language}
              label={t("readAloud", { ns: "kiosk" })}
              volumeLabel={t("volume", { ns: "kiosk" })}
            />
            <div className="welcome-actions">
              <button
                className="ui-button ui-button--primary ui-button--kiosk"
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await intake.consent(consent, true);
                    move("identity");
                  })
                }
              >
                {t("consentContinue", { ns: "kiosk" })}
              </button>
              <button
                className="ui-button ui-button--secondary"
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const result = await intake.consent(consent, false);
                    intake.finish();
                    setError(
                      result.message ?? t("staffMessage", { ns: "kiosk" }),
                    );
                    move("help");
                  })
                }
              >
                {t("consentDecline", { ns: "kiosk" })}
              </button>
            </div>
          </>
        )}

        {stage === "identity" && (
          <>
            <p className="eyebrow">{t("screenS4", { ns: "kiosk" })}</p>
            <h1>{t("identityTitle", { ns: "kiosk" })}</h1>
            {session?.assisted && (
              <>
                <label htmlFor="intake-name">
                  {t("patientNameLabel", { ns: "kiosk" })}
                </label>
                <input
                  id="intake-name"
                  autoComplete="name"
                  maxLength={200}
                  required
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </>
            )}
            <label htmlFor="intake-age">{t("ageLabel", { ns: "kiosk" })}</label>
            <input
              id="intake-age"
              type="number"
              inputMode="numeric"
              min="0"
              max="120"
              value={age}
              onChange={(event) => setAge(event.target.value)}
            />
            <label htmlFor="intake-sex">{t("sexLabel", { ns: "kiosk" })}</label>
            <select
              id="intake-sex"
              value={sex}
              onChange={(event) => setSex(event.target.value)}
            >
              <option value="female">{t("sexFemale", { ns: "kiosk" })}</option>
              <option value="male">{t("sexMale", { ns: "kiosk" })}</option>
              <option value="other">{t("sexOther", { ns: "kiosk" })}</option>
              <option value="unknown">
                {t("sexUnknown", { ns: "kiosk" })}
              </option>
            </select>
            <label htmlFor="intake-phone">
              {t("phoneLabel", { ns: "kiosk" })}
            </label>
            <input
              id="intake-phone"
              inputMode="numeric"
              autoComplete="tel-national"
              maxLength={10}
              value={phone}
              onChange={(event) =>
                setPhone(event.target.value.replace(/\D/g, ""))
              }
            />
            <label className="intake-notification-opt-in" htmlFor="intake-sms-opt-in">
              <input
                id="intake-sms-opt-in"
                type="checkbox"
                checked={smsOptIn}
                disabled={!phone}
                onChange={(event) => setSmsOptIn(event.target.checked)}
              />
              {t("smsOptIn", { ns: "kiosk" })}
            </label>
            <button
              className="ui-button ui-button--primary ui-button--kiosk"
              type="button"
              disabled={
                busy ||
                age === "" ||
                Number(age) < 0 ||
                Number(age) > 120 ||
                (session?.assisted === true && !name.trim()) ||
                (phone !== "" && !/^[6-9]\d{9}$/.test(phone))
              }
              onClick={() =>
                void run(async () => {
                  await intake.identity(
                    Number(age),
                    sex,
                    phone || undefined,
                    session?.assisted ? name.trim() : undefined,
                    smsOptIn,
                  );
                  setTiles((await intake.dangerTiles()).tiles);
                  move("danger");
                })
              }
            >
              {t("continueIntake", { ns: "kiosk" })}
            </button>
          </>
        )}

        {stage === "danger" && (
          <>
            <p className="eyebrow">{t("screenS3", { ns: "kiosk" })}</p>
            <h1>{t("dangerTitle", { ns: "kiosk" })}</h1>
            <div className="kiosk-danger-grid">
              {tiles.map((tile) => (
                <DangerTile
                  key={tile.id}
                  label={tile.label}
                  selected={selectedTiles.includes(tile.id)}
                  onClick={() =>
                    setSelectedTiles((selected) =>
                      selected.includes(tile.id)
                        ? selected.filter((item) => item !== tile.id)
                        : [...selected, tile.id],
                    )
                  }
                />
              ))}
            </div>
            {selectedTiles.length > 0 && (
              <p role="status">{t("staffMessage", { ns: "kiosk" })}</p>
            )}
            <div className="welcome-actions">
              <button
                className="ui-button ui-button--secondary"
                type="button"
                onClick={() => setSelectedTiles([])}
              >
                {t("dangerNone", { ns: "kiosk" })}
              </button>
              <button
                className="ui-button ui-button--primary ui-button--kiosk"
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await intake.dangerSigns(selectedTiles);
                    acceptInterviewResponse(await intake.nextQuestion());
                  })
                }
              >
                {t("nextIntake", { ns: "kiosk" })}
              </button>
            </div>
          </>
        )}

        {stage === "interview" && currentQuestion && (
          <>
            <p className="eyebrow">{t("screenS5", { ns: "kiosk" })}</p>
            <h1>{t("questionTitle", { ns: "kiosk" })}</h1>
            <p lang={consent.language}>{currentQuestion.prompt}</p>
            <AudioPrompt
              id={`question.${currentQuestion.id}`}
              language={consent.language}
              label={t("readAloud", { ns: "kiosk" })}
              volumeLabel={t("volume", { ns: "kiosk" })}
            />
            {currentQuestion.input === "scale" ? (
              <FacesScale
                value={typeof answer === "number" ? answer : undefined}
                onChange={setAnswer}
              />
            ) : (
              <textarea
                aria-label={t("answerPlaceholder", { ns: "kiosk" })}
                maxLength={500}
                rows={4}
                value={String(answer)}
                placeholder={t("answerPlaceholder", { ns: "kiosk" })}
                onChange={(event) => {
                  if (voiceState === "recording") {
                    void stopCapture.current?.();
                    stopCapture.current = null;
                    voiceSocket.current?.close();
                    voiceSocket.current = null;
                    setVoiceState("idle");
                  }
                  setAnswer(event.target.value);
                  setAnswerMode("touch");
                }}
              />
            )}
            <div className="kiosk-interview-tools">
              <VoiceOrb
                disabled={
                  busy ||
                  currentQuestion.input === "scale" ||
                  voiceState === "connecting" ||
                  voiceState === "processing"
                }
                active={voiceState === "recording"}
                label={t(
                  voiceState === "recording" ? "voiceStop" : "voiceStart",
                  { ns: "kiosk" },
                )}
                onClick={() =>
                  voiceState === "recording"
                    ? void stopCapture.current?.()
                    : void startVoice()
                }
              />
              <div>
                <p aria-live="polite">
                  {currentQuestion.input === "scale"
                    ? t("voiceScale", { ns: "kiosk" })
                    : t(`voiceState_${voiceState}`, { ns: "kiosk" })}
                </p>
                {voiceState === "recording" && (
                  <progress
                    aria-label={t("voiceLevel", { ns: "kiosk" })}
                    max={0.1}
                    value={Math.min(voiceLevel, 0.1)}
                  />
                )}
              </div>
            </div>
            <p>{t("voicePermissionExplain", { ns: "kiosk" })}</p>
            <p className="ui-sr-only" aria-live="polite">
              {voiceText}
            </p>
            {voiceError && <p role="status">{voiceError}</p>}
            {voiceState === "review" && (
              <p role="status">{t("voiceLowConfidence", { ns: "kiosk" })}</p>
            )}
            <div className="welcome-actions">
              {!editing && answered.length > 0 && (
                <button
                  className="ui-button ui-button--secondary"
                  type="button"
                  onClick={onPrevious}
                >
                  {t("backQuestion", { ns: "kiosk" })}
                </button>
              )}
              {currentQuestion.optional && !editing && (
                <button
                  className="ui-button ui-button--secondary"
                  type="button"
                  disabled={busy}
                  onClick={() => void submitAnswer(true)}
                >
                  {t("skipQuestion", { ns: "kiosk" })}
                </button>
              )}
              <button
                className="ui-button ui-button--primary ui-button--kiosk"
                type="button"
                disabled={
                  busy ||
                  (!editing && !currentQuestion.optional && answer === "") ||
                  answer === ""
                }
                onClick={() => void submitAnswer()}
              >
                {t("nextIntake", { ns: "kiosk" })}
              </button>
            </div>
          </>
        )}

        {stage === "summary" && caseSheet && (
          <>
            <p className="eyebrow">{t("screenS6", { ns: "kiosk" })}</p>
            <h1>{t("summaryTitle", { ns: "kiosk" })}</h1>
            <div className="kiosk-readback">
              <h2>{t("readbackLabel", { ns: "kiosk" })}</h2>
              <p lang={consent.language}>{readback}</p>
              <ReadBackAudioButton
                label={t("readAloud", { ns: "kiosk" })}
                volumeLabel={t("volume", { ns: "kiosk" })}
              />
            </div>
            <dl className="kiosk-summary">
              <dt>{t("summaryConcern", { ns: "kiosk" })}</dt>
              <dd>{caseSheet.chief_complaint.text_original}</dd>
              {caseSheet.hpi.onset && (
                <>
                  <dt>{t("summaryDuration", { ns: "kiosk" })}</dt>
                  <dd>{caseSheet.hpi.onset.text_original}</dd>
                </>
              )}
              {caseSheet.hpi.severity_0_10 !== undefined && (
                <>
                  <dt>{t("painQuestion", { ns: "kiosk" })}</dt>
                  <dd>{caseSheet.hpi.severity_0_10}/10</dd>
                </>
              )}
              {caseSheet.hpi.associated_symptoms?.map((item, index) => (
                <Fragment key={`symptom-${index}`}>
                  <dt>{t("summaryOther", { ns: "kiosk" })}</dt>
                  <dd>{item.text_original}</dd>
                </Fragment>
              ))}
              {caseSheet.past_history.conditions.map((item) => (
                <Fragment key={item}>
                  <dt>{t("summaryHistory", { ns: "kiosk" })}</dt>
                  <dd>{item}</dd>
                </Fragment>
              ))}
              {caseSheet.medications.map((item, index) => (
                <Fragment key={`medicine-${index}`}>
                  <dt>{t("summaryMedications", { ns: "kiosk" })}</dt>
                  <dd>{item.name}</dd>
                </Fragment>
              ))}
              {caseSheet.allergies.map((item, index) => (
                <Fragment key={`allergy-${index}`}>
                  <dt>{t("summaryAllergies", { ns: "kiosk" })}</dt>
                  <dd>{item.text_original}</dd>
                </Fragment>
              ))}
              {caseSheet.family_history.map((item, index) => (
                <Fragment key={`family-${index}`}>
                  <dt>{t("summaryFamilyHistory", { ns: "kiosk" })}</dt>
                  <dd>{item.text_original}</dd>
                </Fragment>
              ))}
            </dl>
            {caseSheet.red_flags.length > 0 && (
              <p role="status">{t("staffMessage", { ns: "kiosk" })}</p>
            )}
            <div className="kiosk-summary-changes">
              {answered.map((item) => (
                <button
                  className="ui-button ui-button--secondary"
                  type="button"
                  key={item.id}
                  onClick={() => {
                    setEditing(item);
                    setAnswer(item.answer);
                    move("interview");
                  }}
                >
                  {t("change", { ns: "kiosk" })}: {item.prompt}
                </button>
              ))}
            </div>
            {needsAssistance && (
              <p role="status">{t("needsAssistance", { ns: "kiosk" })}</p>
            )}
            <button
              className="ui-button ui-button--primary ui-button--kiosk"
              type="button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const result = await intake.confirm();
                  setQueueToken({
                    token: result.token,
                    statusUrl: result.status_url,
                  });
                  intake.finish();
                  move("done");
                })
              }
            >
              {t("confirmIntake", { ns: "kiosk" })}
            </button>
          </>
        )}

        {stage === "done" && queueToken && (
          <div className="token-receipt">
            <h1>{t("doneTitle", { ns: "kiosk" })}</h1>
            <p>{t("queueToken", { ns: "kiosk" })}</p>
            <strong className="status-token">{queueToken.token}</strong>
            <a href={queueToken.statusUrl}>
              <QRCodeSVG
                value={new URL(
                  queueToken.statusUrl,
                  window.location.origin,
                ).toString()}
                size={180}
                title={t("statusQrLabel", { ns: "kiosk" })}
              />
            </a>
            <p>{t("statusLink", { ns: "kiosk" })}</p>
            <button
              className="ui-button ui-button--secondary no-print"
              type="button"
              onClick={() => window.print()}
            >
              {t("printReceipt", { ns: "kiosk" })}
            </button>
            <button
              className="ui-button ui-button--primary ui-button--kiosk no-print"
              type="button"
              onClick={() => navigate("/kiosk", { replace: true })}
            >
              {t("restartPreview", { ns: "kiosk" })}
            </button>
          </div>
        )}
        {stage === "help" && (
          <>
            <h1>{t("staffMessage", { ns: "kiosk" })}</h1>
            <button
              className="ui-button ui-button--primary ui-button--kiosk"
              type="button"
              onClick={() => navigate("/kiosk", { replace: true })}
            >
              {t("restartPreview", { ns: "kiosk" })}
            </button>
          </>
        )}
      </section>
    </main>
  );
}
