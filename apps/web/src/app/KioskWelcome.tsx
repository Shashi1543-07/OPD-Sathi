import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useOutletContext } from "react-router-dom";
import { Siren } from "lucide-react";
import { LanguageSelector } from "../components/ui";
import { EmergencyNotice } from "./EmergencyNotice";
import { intake } from "../lib/intake";
import { useLanguageStore } from "../languageStore";

export function KioskWelcome() {
  const { t } = useTranslation(["common", "kiosk"]);
  const language = useLanguageStore((state) => state.language);
  const navigate = useNavigate();
  const location = useLocation();
  const outletContext = useOutletContext<{ networkOffline: boolean } | undefined>();
  const networkOffline = outletContext?.networkOffline ?? !navigator.onLine;
  const [activationCode, setActivationCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [activated, setActivated] = useState(intake.isKioskActivated());

  // Setup convenience: open /kiosk?activate=<one-time code> once on the kiosk device.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const code = params.get("activate");
    if (!code || activated) return;
    params.delete("activate");
    navigate({ pathname: "/kiosk", search: params.toString() }, { replace: true });
    void Promise.resolve()
      .then(() => {
        setBusy(true);
        return intake.activate(code.trim());
      })
      .then(() => setActivated(true))
      .catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : t("errorTitle", { ns: "kiosk" })),
      )
      .finally(() => setBusy(false));
    // Runs once for the URL present when the welcome screen opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activate = async () => {
    setBusy(true);
    setError("");
    try {
      await intake.activate(activationCode.trim());
      setActivationCode("");
      setActivated(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("errorTitle", { ns: "kiosk" }));
    } finally {
      setBusy(false);
    }
  };

  const [emergency, setEmergency] = useState<"sending" | "sent" | "failed">();
  const callEmergency = () => {
    setEmergency("sending");
    intake
      .emergency()
      .then(() => setEmergency("sent"))
      .catch(() => setEmergency("failed"));
  };

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      await intake.start(language === "hi" || language === "mr" ? language : "en");
      navigate({ pathname: "/kiosk/intake", search: location.search });
    } catch (cause) {
      if (!intake.isKioskActivated()) setActivated(false);
      setError(cause instanceof Error ? cause.message : t("errorTitle", { ns: "kiosk" }));
    } finally {
      setBusy(false);
    }
  };

  if (emergency) {
    return (
      <main className="kiosk-main">
        <EmergencyNotice state={emergency} onDone={() => setEmergency(undefined)} />
      </main>
    );
  }

  return (
    <main className="kiosk-main">
      {activated && (
        <section className="emergency-entry" aria-label={t("emergencyButton", { ns: "kiosk" })}>
          <button
            type="button"
            className="emergency-button"
            onClick={callEmergency}
          >
            <Siren aria-hidden="true" size={30} />
            {t("emergencyButton", { ns: "kiosk" })}
          </button>
          <p>{t("emergencyHint", { ns: "kiosk" })}</p>
        </section>
      )}
      <section className="welcome-card">
        <p className="eyebrow">{t("clinicName", { ns: "kiosk" })}</p>
        <h1>{t("welcome", { ns: "common" })}</h1>
        <p className="welcome-intro">{t("chooseLanguage", { ns: "kiosk" })}</p>
        <LanguageSelector />
        <div className="welcome-actions">
          {activated ? (
            <button
              className="ui-button ui-button--primary ui-button--kiosk kiosk-welcome-cta"
              type="button"
              disabled={busy || networkOffline}
              onClick={start}
            >
              {busy ? "…" : t("startIntake", { ns: "kiosk" })}
            </button>
          ) : (
            <div className="kiosk-activation">
              <h2>{t("activationTitle", { ns: "kiosk" })}</h2>
              <label htmlFor="kiosk-activation-code">
                {t("activationCode", { ns: "kiosk" })}
              </label>
              <input
                id="kiosk-activation-code"
                autoComplete="one-time-code"
                value={activationCode}
                onChange={(event) => setActivationCode(event.target.value)}
                maxLength={64}
              />
              <button
                className="ui-button ui-button--primary ui-button--kiosk"
                type="button"
                disabled={busy || activationCode.trim().length < 40}
                onClick={activate}
              >
                {busy ? "…" : t("activate", { ns: "kiosk" })}
              </button>
              <p>{t("activationHelp", { ns: "kiosk" })}</p>
            </div>
          )}
          {activated && networkOffline && (
            <button
              className="ui-button ui-button--secondary ui-button--kiosk"
              type="button"
              onClick={() => navigate("/kiosk/offline-intake")}
            >
              {t("offlineStart", { ns: "kiosk" })}
            </button>
          )}
          {error && <p role="alert">{error}</p>}
        </div>
      </section>
    </main>
  );
}
