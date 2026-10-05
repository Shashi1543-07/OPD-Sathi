import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { LanguageSelector } from "../components/ui";

export function KioskWelcome() {
  const { t } = useTranslation(["common", "kiosk"]);
  return (
    <main className="kiosk-main">
      <section className="welcome-card">
        <p className="eyebrow">{t("clinicName", { ns: "kiosk" })}</p>
        <h1>{t("welcome", { ns: "common" })}</h1>
        <p className="welcome-intro">{t("chooseLanguage", { ns: "kiosk" })}</p>
        <LanguageSelector />
        <div className="welcome-actions">
          <Link
            className="ui-button ui-button--primary ui-button--kiosk"
            to="/kiosk/preview/s2"
          >
            {t("startPreview", { ns: "kiosk" })}
          </Link>
          <p>{t("previewNotice", { ns: "kiosk" })}</p>
        </div>
      </section>
    </main>
  );
}
