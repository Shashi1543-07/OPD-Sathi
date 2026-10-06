import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { Language } from "../i18n";
import { KioskIntake } from "./KioskIntake";
import { intake } from "../lib/intake";
import { staffAuth } from "../lib/auth";

export function NurseConsole() {
  const { t } = useTranslation("staff");
  const [language, setLanguage] = useState<Language>("en");
  const [started, setStarted] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  if (staffAuth.user?.role !== "nurse") {
    return (
      <section className="nurse-console">
        <h2>{t("patients")}</h2>
        <p>{t("nurseOnly")}</p>
      </section>
    );
  }
  if (started) {
    return (
      <section className="nurse-console nurse-console--intake">
        <header>
          <h2>{t("assistedRegistration")}</h2>
          <Link to="/staff/queue">{t("backToQueue")}</Link>
        </header>
        <KioskIntake />
      </section>
    );
  }

  async function beginRegistration() {
    setWorking(true);
    setError("");
    try {
      await intake.startAssisted(language);
      setStarted(true);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : t("assistedStartError"),
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <section className="nurse-console">
      <header>
        <div>
          <h2>{t("nurseConsoleTitle")}</h2>
          <p>{t("nurseConsoleDescription")}</p>
        </div>
        <Link to="/staff/queue">{t("reviewAlertsQueue")}</Link>
      </header>
      <article className="nurse-registration-card">
        <h3>{t("assistedRegistration")}</h3>
        <p>{t("consentBeforeRegistration")}</p>
        <label htmlFor="assisted-language">{t("preferredLanguage")}</label>
        <select
          id="assisted-language"
          value={language}
          onChange={(event) => setLanguage(event.target.value as Language)}
        >
          <option value="en">{t("languageEnglish")}</option>
          <option value="hi">{t("languageHindi")}</option>
          <option value="mr">{t("languageMarathi")}</option>
        </select>
        {error && <p role="alert">{error}</p>}
        <button
          type="button"
          disabled={working}
          onClick={() => void beginRegistration()}
        >
          {working ? t("startingRegistration") : t("beginRegistration")}
        </button>
      </article>
      <p className="nurse-console-note">{t("nurseQueueTools")}</p>
    </section>
  );
}
