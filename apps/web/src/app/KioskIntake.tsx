import { Fragment, useEffect, useMemo, useState } from "react";
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

type Stage =
  "consent" | "identity" | "danger" | "interview" | "summary" | "done" | "help";

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
  const [tiles, setTiles] = useState<DangerOption[]>([]);
  const [selectedTiles, setSelectedTiles] = useState<string[]>([]);
  const [question, setQuestion] = useState<Question>();
  const [answered, setAnswered] = useState<
    (Question & { answer: string | number })[]
  >([]);
  const [editing, setEditing] = useState<Question>();
  const [answer, setAnswer] = useState<string | number>("");
  const [caseSheet, setCaseSheet] = useState<CaseSheet>();
  const [readback, setReadback] = useState("");
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
        ? await intake.correct(editing.id, answer)
        : await intake.answer(currentQuestion.id, skip ? null : answer, skip);
      acceptInterviewResponse(response);
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
            {currentQuestion.input === "scale" ? (
              <FacesScale
                value={typeof answer === "number" ? answer : undefined}
                onChange={setAnswer}
              />
            ) : (
              <textarea
                maxLength={500}
                rows={4}
                value={String(answer)}
                placeholder={t("answerPlaceholder", { ns: "kiosk" })}
                onChange={(event) => setAnswer(event.target.value)}
              />
            )}
            <div className="kiosk-interview-tools">
              <VoiceOrb disabled />
              <span>{t("voiceComing", { ns: "kiosk" })}</span>
            </div>
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
