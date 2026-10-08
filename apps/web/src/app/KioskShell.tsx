import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useLanguageStore } from "../languageStore";
import { Contrast, Volume2, VolumeX } from "lucide-react";
import {
  ConfirmDialog,
  HelpButton,
  IdleWarningDialog,
  OfflineBanner,
} from "../components/ui";
import { AudioPrompt } from "../components/ui/AudioPrompt";
import { intake } from "../lib/intake";
import { useKioskPrefs } from "../lib/kioskPrefs";
import { stopSpeaking } from "../lib/voice";
import "./screens.css";

const IDLE_MS = 60_000;
const WARNING_MS = 15_000;

export function KioskShell() {
  const { t } = useTranslation("common");
  const language = useLanguageStore((state) => state.language);
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const navigate = useNavigate();
  const location = useLocation();
  const judgeMode = new URLSearchParams(location.search).get("demo") === "1";
  const hospitalLogo = import.meta.env.VITE_HOSPITAL_LOGO_URL?.trim();
  const [warning, setWarning] = useState(false);
  const [highContrast, setHighContrast] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpState, setHelpState] = useState<"ask" | "sending" | "sent" | "failed">("ask");
  const voiceGuidance = useKioskPrefs((state) => state.voiceGuidance);
  const setVoiceGuidance = useKioskPrefs((state) => state.setVoiceGuidance);
  const [idleKey, setIdleKey] = useState(0);
  const [networkOffline, setNetworkOffline] = useState(!navigator.onLine);
  const lastActivity = useRef(0);

  const restart = useCallback(() => {
    setWarning(false);
    setIdleKey((key) => key + 1);
  }, []);
  const resetSession = useCallback(() => {
    void intake.cancel();
    setWarning(false);
    setHighContrast(false);
    setHelpOpen(false);
    setLanguage("en");
    navigate(
      { pathname: "/kiosk", search: location.search },
      { replace: true },
    );
  }, [location.search, navigate, setLanguage]);

  useEffect(() => {
    let disposed = false;
    const checkHealth = async () => {
      if (!navigator.onLine) {
        setNetworkOffline(true);
        return;
      }
      try {
        const response = await fetch(
          `${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}/healthz`,
          {
            cache: "no-store",
            signal: AbortSignal.timeout(4_000),
          },
        );
        if (!disposed) setNetworkOffline(!response.ok);
      } catch {
        if (!disposed) setNetworkOffline(true);
      }
    };
    const onOnline = () => void checkHealth();
    const onOffline = () => setNetworkOffline(true);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    void checkHealth();
    const timer = window.setInterval(() => void checkHealth(), 10_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

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
      className={`kiosk-app ${highContrast ? "is-high-contrast" : ""}`}
      data-layout="kiosk"
    >
      <header className="kiosk-header">
        <div className="kiosk-brand">
          {hospitalLogo ? (
            <img
              className="kiosk-mark kiosk-hospital-logo"
              src={hospitalLogo}
              alt=""
            />
          ) : (
            <span className="kiosk-mark" aria-hidden="true">
              O
            </span>
          )}
          <div>
            <strong>{t("appName")}</strong>
            <small>{t("clinicName", { ns: "kiosk" })}</small>
          </div>
        </div>
        <div className="kiosk-tools">
          {location.pathname !== "/kiosk" && (
            <div className="kiosk-tool-item">
              <button
                type="button"
                className="ui-tool-button"
                aria-pressed={voiceGuidance}
                aria-label={t(voiceGuidance ? "soundOn" : "soundOff", { ns: "intake" })}
                title={t(voiceGuidance ? "soundOn" : "soundOff", { ns: "intake" })}
                onClick={() => {
                  if (voiceGuidance) stopSpeaking();
                  setVoiceGuidance(!voiceGuidance);
                }}
              >
                {voiceGuidance ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}
              </button>
            </div>
          )}
          <div className="kiosk-tool-item">
            <button
              type="button"
              className="ui-tool-button"
              onClick={() => setHighContrast((current) => !current)}
              aria-pressed={highContrast}
              aria-label={
                highContrast
                  ? t("highContrastOff", { ns: "kiosk" })
                  : t("highContrast", { ns: "kiosk" })
              }
              title={`${t("highContrast", { ns: "kiosk" })} — ${t("highContrastDesc", { ns: "kiosk" })}`}
            >
              <Contrast aria-hidden="true" />
            </button>
            <div className="ui-tooltip" role="tooltip">
              <strong>{t("highContrast", { ns: "kiosk" })}</strong>
              <span>{t("highContrastDesc", { ns: "kiosk" })}</span>
            </div>
          </div>
          <HelpButton
            variant="tool"
            onClick={() => {
              setHelpState("ask");
              setHelpOpen(true);
            }}
            tooltipTitle={t("callStaff")}
            tooltipDesc={t("callStaffDesc", { ns: "kiosk" })}
          />
          {location.pathname === "/kiosk" && (
            <AudioPrompt
              language={language}
              promptId="ui.welcome"
              text={t("welcome")}
              variant="tool"
              tooltipTitle={t("readAloud")}
              tooltipDesc={t("readAloudDesc", { ns: "kiosk" })}
            />
          )}
        </div>
      </header>
      {networkOffline && <OfflineBanner />}
      {judgeMode && (
        <div className="demo-ribbon" role="note">
          {t("judgeRibbon")}
        </div>
      )}
      <Outlet context={{ networkOffline }} />
      <footer className="kiosk-footer">
        <span>{t("historyOnly")}</span>
        {judgeMode && <span>{t("demoNotice")}</span>}
      </footer>
      <IdleWarningDialog
        open={warning}
        onContinue={restart}
        onEnd={resetSession}
      />
      <ConfirmDialog
        open={helpOpen}
        title={
          helpState === "sent"
            ? t("helpSentTitle", { ns: "intake" })
            : t("callStaff")
        }
        confirmLabel={
          helpState === "ask" && intake.session()
            ? t("helpYes", { ns: "intake" })
            : t("close")
        }
        cancelLabel={t("helpNo", { ns: "intake" })}
        onConfirm={() => {
          if (helpState !== "ask" || !intake.session()) {
            setHelpOpen(false);
            return;
          }
          setHelpState("sending");
          intake
            .requestHelp()
            .then(() => setHelpState("sent"))
            .catch(() => setHelpState("failed"));
        }}
        onCancel={() => setHelpOpen(false)}
      >
        <p>
          {!intake.session()
            ? t("helpPrompt", { ns: "kiosk" })
            : helpState === "sent"
              ? t("helpSent", { ns: "intake" })
              : helpState === "failed"
                ? t("helpFailed", { ns: "intake" })
                : helpState === "sending"
                  ? t("working", { ns: "intake" })
                  : t("helpAsk", { ns: "intake" })}
        </p>
      </ConfirmDialog>
    </div>
  );
}
