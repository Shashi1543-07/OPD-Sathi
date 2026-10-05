import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useNavigate } from "react-router-dom";
import { useLanguageStore } from "../languageStore";
import {
  Button,
  ConfirmDialog,
  HelpButton,
  IdleWarningDialog,
} from "../components/ui";
import { AudioPrompt } from "../components/ui/AudioPrompt";
import "./screens.css";

const IDLE_MS = 60_000;
const WARNING_MS = 15_000;

export function KioskShell() {
  const { t } = useTranslation("common");
  const language = useLanguageStore((state) => state.language);
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const navigate = useNavigate();
  const [warning, setWarning] = useState(false);
  const [largeText, setLargeText] = useState(false);
  const [highContrast, setHighContrast] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [idleKey, setIdleKey] = useState(0);
  const lastActivity = useRef(0);

  const restart = useCallback(() => {
    setWarning(false);
    setIdleKey((key) => key + 1);
  }, []);
  const resetSession = useCallback(() => {
    setWarning(false);
    setLargeText(false);
    setHighContrast(false);
    setHelpOpen(false);
    setLanguage("en");
    navigate("/kiosk", { replace: true });
  }, [navigate, setLanguage]);

  useEffect(() => {
    const handleActivity = () => {
      const now = Date.now();
      if (now - lastActivity.current < 1000) return;
      lastActivity.current = now;
      if (warning) setWarning(false);
      setIdleKey((key) => key + 1);
    };
    const events: (keyof WindowEventMap)[] = [
      "pointerdown",
      "pointermove",
      "keydown",
      "touchstart",
      "wheel",
    ];
    events.forEach((event) =>
      window.addEventListener(event, handleActivity, { passive: true }),
    );
    return () =>
      events.forEach((event) =>
        window.removeEventListener(event, handleActivity),
      );
  }, [warning]);

  useEffect(() => {
    if (warning) {
      const timer = window.setTimeout(resetSession, WARNING_MS);
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => setWarning(true), IDLE_MS);
    return () => window.clearTimeout(timer);
  }, [warning, idleKey, resetSession]);

  return (
    <div
      className={`kiosk-app ${largeText ? "is-large-text" : ""} ${highContrast ? "is-high-contrast" : ""}`}
      data-layout="kiosk"
    >
      <header className="kiosk-header">
        <div className="kiosk-brand">
          <span className="kiosk-mark" aria-hidden="true">
            O
          </span>
          <div>
            <strong>{t("appName")}</strong>
            <small>{t("clinicName", { ns: "kiosk" })}</small>
          </div>
        </div>
        <div className="kiosk-tools">
          <div
            className="kiosk-language-selector"
            role="group"
            aria-label={t("chooseLanguage", { ns: "kiosk" })}
          >
            {[
              { code: "hi" as const, label: "हिन्दी" },
              { code: "mr" as const, label: "मराठी" },
              { code: "en" as const, label: "EN" },
            ].map(({ code, label }) => (
              <button
                type="button"
                lang={code}
                aria-pressed={language === code}
                key={code}
                onClick={() => setLanguage(code)}
              >
                {label}
              </button>
            ))}
          </div>
          <Button
            variant="secondary"
            onClick={() => setLargeText((current) => !current)}
            aria-pressed={largeText}
          >
            {largeText ? t("standardText") : t("largeText", { ns: "kiosk" })}
          </Button>
          <Button
            variant="secondary"
            onClick={() => setHighContrast((current) => !current)}
            aria-pressed={highContrast}
          >
            {highContrast
              ? t("highContrastOff", { ns: "kiosk" })
              : t("highContrast", { ns: "kiosk" })}
          </Button>
          <HelpButton onClick={() => setHelpOpen(true)} />
          <AudioPrompt language={language} promptId="ui.welcome" />
        </div>
      </header>
      <Outlet />
      <footer className="kiosk-footer">
        <span>{t("historyOnly")}</span>
        <span>{t("demoNotice")}</span>
      </footer>
      <IdleWarningDialog
        open={warning}
        onContinue={restart}
        onEnd={resetSession}
      />
      <ConfirmDialog
        open={helpOpen}
        title={t("callStaff")}
        onConfirm={() => setHelpOpen(false)}
        onCancel={() => setHelpOpen(false)}
      >
        <p>{t("helpPrompt", { ns: "kiosk" })}</p>
      </ConfirmDialog>
    </div>
  );
}
