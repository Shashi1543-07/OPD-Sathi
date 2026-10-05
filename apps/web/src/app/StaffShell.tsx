import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, ConnectionIndicator, Toast } from "../components/ui";

export function StaffShell() {
  const { t } = useTranslation(["common", "staff"]);
  const [notice, setNotice] = useState(false);
  return (
    <div className="staff-app" data-layout="staff">
      <aside className="staff-sidebar">
        <a className="staff-brand" href="/staff">
          {t("appName", { ns: "common" })}
        </a>
        <nav aria-label={t("staffNavigation", { ns: "staff" })}>
          {[
            "dashboard",
            "queue",
            "consultations",
            "patients",
            "reports",
            "settings",
          ].map((key) => (
            <a href="#" key={key} onClick={(event) => event.preventDefault()}>
              {t(key, { ns: "staff" })}
            </a>
          ))}
        </nav>
        <span className="staff-demo-label">
          {t("demoNotice", { ns: "common" })}
        </span>
      </aside>
      <div className="staff-content">
        <header className="staff-topbar">
          <div>
            <p className="eyebrow">{t("staffTitle", { ns: "staff" })}</p>
            <h1>{t("signIn", { ns: "staff" })}</h1>
          </div>
          <ConnectionIndicator state="live" />
        </header>
        <main className="staff-main">
          <section className="staff-login-card">
            <h2>{t("staffWelcome", { ns: "staff" })}</h2>
            <p>{t("staffSubtitle", { ns: "staff" })}</p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setNotice(true);
              }}
            >
              <label htmlFor="staff-email">{t("email", { ns: "staff" })}</label>
              <input
                id="staff-email"
                type="email"
                autoComplete="username"
                placeholder={t("emailPlaceholder", { ns: "staff" })}
              />
              <label htmlFor="staff-password">
                {t("password", { ns: "staff" })}
              </label>
              <input
                id="staff-password"
                type="password"
                autoComplete="current-password"
              />
              <Button type="submit">{t("signIn", { ns: "staff" })}</Button>
            </form>
            <div className="staff-notice">
              {notice ? (
                <Toast tone="info">
                  {t("demoLoginResult", { ns: "staff" })}
                </Toast>
              ) : (
                <p>{t("demoLoginNote", { ns: "staff" })}</p>
              )}
            </div>
            <p className="staff-lockout">
              {t("lockoutNotice", { ns: "staff" })}
            </p>
          </section>
        </main>
      </div>
    </div>
  );
}
