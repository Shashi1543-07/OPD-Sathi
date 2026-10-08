import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import { useLanguageStore } from "../languageStore";
import { AudioPrompt } from "../components/ui/AudioPrompt";
import {
  Button,
  ChoiceButton,
  DangerTile,
  NumberPad,
  ProgressDots,
} from "../components/ui";

const steps = ["s2", "s3", "s4", "s5", "s6", "s7"] as const;
type PreviewStep = (typeof steps)[number];

export function KioskPreview() {
  const { step } = useParams<{ step: string }>();
  const current = steps.includes(step as PreviewStep)
    ? (step as PreviewStep)
    : "s2";
  const index = steps.indexOf(current);
  const { t } = useTranslation(["common", "kiosk", "status"]);
  const language = useLanguageStore((state) => state.language);
  const [age, setAge] = useState("");
  const [phone, setPhone] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [choice, setChoice] = useState("");

  const heading = t(`screen${current.toUpperCase()}`, { ns: "kiosk" });
  return (
    <main className="kiosk-main">
      <article className="preview-card">
        <div className="preview-topline">
          <span className="preview-label">
            {t("previewBadge", { ns: "kiosk" })}
          </span>
          <ProgressDots current={index + 1} total={steps.length} />
        </div>
        <h1>{heading}</h1>
        <p className="preview-description">
          {t(`description${current.toUpperCase()}`, { ns: "kiosk" })}
        </p>
        {current === "s2" && (
          <div className="preview-panel">
            <p>{t("consentText", { ns: "kiosk" })}</p>
            <AudioPrompt
              language={language}
              promptId="ui.consent"
              text={t("consentText", { ns: "kiosk" })}
            />
            <div className="preview-actions">
              <ChoiceButton
                selected={choice === "agree"}
                onClick={() => setChoice("agree")}
              >
                {t("agree", { ns: "kiosk" })}
              </ChoiceButton>
              <ChoiceButton
                selected={choice === "help"}
                onClick={() => setChoice("help")}
              >
                {t("noAgree", { ns: "kiosk" })}
              </ChoiceButton>
            </div>
          </div>
        )}
        {current === "s3" && (
          <>
            <p>{t("dangerQuestion", { ns: "kiosk" })}</p>
            <div className="danger-grid">
              {Array.from({ length: 8 }, (_, i) => {
                const option = `sampleConcern${i + 1}`;
                return (
                  <DangerTile
                    key={option}
                    label={t(option, { ns: "kiosk" })}
                    selected={selected.includes(option)}
                    onClick={() =>
                      setSelected((items) =>
                        items.includes(option)
                          ? items.filter((item) => item !== option)
                          : [...items, option],
                      )
                    }
                  />
                );
              })}
            </div>
            <ChoiceButton
              selected={selected.includes("none")}
              onClick={() =>
                setSelected((items) =>
                  items.includes("none")
                    ? items.filter((item) => item !== "none")
                    : ["none"],
                )
              }
            >
              {t("noneOfThese", { ns: "kiosk" })}
            </ChoiceButton>
          </>
        )}
        {current === "s4" && (
          <div className="preview-panel">
            <p className="preview-label">{t("age", { ns: "kiosk" })}</p>
            <output
              id="sample-age"
              className="sample-output"
              aria-label={t("age", { ns: "kiosk" })}
              aria-live="polite"
            >
              {age || "—"}
            </output>
            <NumberPad value={age} onChange={setAge} maxLength={3} />
            <p>{t("sex", { ns: "kiosk" })}</p>
            <div className="preview-actions">
              {["female", "male", "another", "preferNotSay"].map((key) => (
                <ChoiceButton
                  key={key}
                  selected={choice === key}
                  onClick={() => setChoice(key)}
                >
                  {t(key, { ns: "kiosk" })}
                </ChoiceButton>
              ))}
            </div>
            <label className="preview-label" htmlFor="sample-phone">
              {t("optionalPhone", { ns: "kiosk" })}
            </label>
            <input
              id="sample-phone"
              type="tel"
              autoComplete="off"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              placeholder={t("phonePlaceholder", { ns: "kiosk" })}
            />
            <Button variant="secondary">{t("skip", { ns: "kiosk" })}</Button>
          </div>
        )}
        {current === "s5" && (
          <div className="preview-panel">
            <p className="sample-question">
              {t("sampleQuestion", { ns: "kiosk" })}
            </p>
            <div
              className="sample-orb"
              role="img"
              aria-label={t("voicePlaceholder", { ns: "kiosk" })}
            >
              ●
            </div>
            <p>{t("voicePlaceholder", { ns: "kiosk" })}</p>
            <div className="sample-transcript">
              {t("sampleTranscript", { ns: "kiosk" })}
            </div>
            <div className="preview-actions">
              <Button variant="secondary">
                {t("useTouch", { ns: "kiosk" })}
              </Button>
              <Button variant="ghost">{t("skip", { ns: "kiosk" })}</Button>
            </div>
          </div>
        )}
        {current === "s6" && (
          <div className="preview-panel">
            <AudioPrompt
              language={language}
              promptId="ui.confirm"
              text={t("yesCorrect", { ns: "kiosk" })}
            />
            <ul className="summary-list">
              {["summaryConcern", "summaryDuration", "summaryHistory"].map(
                (key) => (
                  <li key={key}>
                    <span>{t(key, { ns: "kiosk" })}</span>
                    <div>
                      <Button variant="secondary">
                        {t("correct", { ns: "kiosk" })}
                      </Button>
                      <Button variant="ghost">
                        {t("change", { ns: "kiosk" })}
                      </Button>
                    </div>
                  </li>
                ),
              )}
            </ul>
            <ChoiceButton
              selected={choice === "confirmed"}
              onClick={() => setChoice("confirmed")}
            >
              {t("yesCorrect", { ns: "kiosk" })}
            </ChoiceButton>
          </div>
        )}
        {current === "s7" && (
          <div className="token-card">
            <span className="preview-label">
              {t("tokenLabel", { ns: "kiosk" })}
            </span>
            <strong>DEMO-024</strong>
            <p>{t("department", { ns: "kiosk" })}</p>
            <div className="token-stats">
              <span>
                {t("position", { ns: "kiosk" })}
                <b>3</b>
              </span>
              <span>
                {t("estimatedWait", { ns: "kiosk" })}
                <b>~20 min</b>
              </span>
            </div>
            <QRCodeSVG
              value="https://opd-saathi.invalid/s/demo"
              size={144}
              aria-label={t("sampleQr", { ns: "kiosk" })}
            />
            <p>{t("scanPhone", { ns: "kiosk" })}</p>
            <p>{t("waitNearby", { ns: "kiosk" })}</p>
            <Button variant="secondary" onClick={() => window.print()}>
              {t("print", { ns: "kiosk" })}
            </Button>
          </div>
        )}
        <nav
          className="preview-nav"
          aria-label={t("previewNavigation", { ns: "kiosk" })}
        >
          <Link
            className="ui-button ui-button--secondary ui-button--md"
            to={index === 0 ? "/kiosk" : `/kiosk/preview/${steps[index - 1]}`}
          >
            {t("back", { ns: "kiosk" })}
          </Link>
          {index < steps.length - 1 && (
            <Link
              className="ui-button ui-button--primary ui-button--md"
              to={`/kiosk/preview/${steps[index + 1]}`}
            >
              {t("continue", { ns: "common" })}
            </Link>
          )}
          {current === "s7" && (
            <Link
              className="ui-button ui-button--secondary ui-button--md"
              to="/kiosk"
            >
              {t("restartPreview", { ns: "kiosk" })}
            </Link>
          )}
        </nav>
        <p className="preview-footnote">{t("historyOnly", { ns: "common" })}</p>
      </article>
    </main>
  );
}
