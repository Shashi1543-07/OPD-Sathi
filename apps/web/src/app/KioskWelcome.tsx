import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { LanguageSelector } from "../components/ui";
import { intake } from "../lib/intake";
import { useLanguageStore } from "../languageStore";

export function KioskWelcome() {
  const { t } = useTranslation(["common", "kiosk"]);
  const language = useLanguageStore((state) => state.language);
  const navigate = useNavigate();
  const [activationCode, setActivationCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const activated = intake.isKioskActivated();

  const activate = async () => {
    setBusy(true);
    setError("");
    try {
      await intake.activate(activationCode.trim());
      setActivationCode("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("errorTitle", { ns: "kiosk" }));
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      await intake.start(language === "hi" || language === "mr" ? language : "en");
      navigate("/kiosk/intake");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("errorTitle", { ns: "kiosk" }));
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="kiosk-main">
      <section className="welcome-card">
        <p className="eyebrow">{t("clinicName", { ns: "kiosk" })}</p>
        <h1>{t("welcome", { ns: "common" })}</h1>
        <p className="welcome-intro">{t("chooseLanguage", { ns: "kiosk" })}</p>
        <LanguageSelector />
        <div className="welcome-actions">
          {activated ? (
            <button className="ui-button ui-button--primary ui-button--kiosk" type="button" disabled={busy} onClick={start}>
              {busy ? "…" : t("startIntake", { ns: "kiosk" })}
            </button>
          ) : (
            <div className="kiosk-activation">
              <h2>{t("activationTitle", { ns: "kiosk" })}</h2>
              <label htmlFor="kiosk-activation-code">{t("activationCode", { ns: "kiosk" })}</label>
              <input
                id="kiosk-activation-code"
                autoComplete="one-time-code"
                value={activationCode}
                onChange={(event) => setActivationCode(event.target.value)}
                maxLength={64}
              />
              <button className="ui-button ui-button--primary ui-button--kiosk" type="button" disabled={busy || activationCode.trim().length < 40} onClick={activate}>
                {busy ? "…" : t("activate", { ns: "kiosk" })}
              </button>
              <p>{t("activationHelp", { ns: "kiosk" })}</p>
            </div>
          )}
          {error && <p role="alert">{error}</p>}
          {import.meta.env.DEV && (
            <div>
              <Link className="ui-button ui-button--secondary kiosk-preview-link" to="/kiosk/preview/s2">
                {t("startPreview", { ns: "kiosk" })}
              </Link>
              <p>{t("previewNotice", { ns: "kiosk" })}</p>
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
