import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { Smartphone, Ticket } from "lucide-react";
import { LanguageSelector } from "../components/ui";
import { intake, IntakeRequestError, type MobileTicket } from "../lib/intake";
import { secureVoiceAvailable } from "../lib/voice";
import { useLanguageStore } from "../languageStore";

type Load = "checking" | "ready" | "invalid" | "paused" | "offline";

/**
 * A patient's own phone after scanning the hospital's check-in QR (/m/<code>). It runs the
 * kiosk's interview against the same server, so the visit, safety checks and token are the
 * kiosk's. A visit already in progress is resumed, and a token issued today is shown again,
 * so a refresh or a second scan never creates a duplicate visit.
 */
export function MobileCheckin() {
  const { code = "" } = useParams<{ code: string }>();
  const { t } = useTranslation(["kiosk", "common"]);
  const navigate = useNavigate();
  const language = useLanguageStore((state) => state.language);
  const outlet = useOutletContext<{ networkOffline: boolean } | undefined>();
  const networkOffline = outlet?.networkOffline ?? !navigator.onLine;
  const [load, setLoad] = useState<Load>("checking");
  const [hospital, setHospital] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ticket] = useState<MobileTicket | null>(() => intake.lastTicket());
  const [inProgress, setInProgress] = useState(() => {
    const session = intake.session();
    return session?.channel === "mobile" && session.checkin === code ? session : null;
  });

  const check = useCallback(async () => {
    try {
      const details = await intake.checkinDetails(code);
      setHospital(details.hospital_name);
      intake.rememberHospitalName(details.hospital_name);
      setLoad("ready");
    } catch (cause) {
      if (cause instanceof IntakeRequestError) {
        // 401: the link is not even shaped like a check-in code.
        setLoad(cause.code === "CHECKIN_TURNED_OFF" ? "paused" : "invalid");
      } else {
        setLoad("offline");
      }
    }
  }, [code]);

  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => (active ? check() : undefined));
    return () => {
      active = false;
    };
  }, [check]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      await intake.startMobile(code, language === "hi" || language === "mr" ? language : "en");
      navigate(`/m/${code}/intake`);
    } catch (cause) {
      if (cause instanceof IntakeRequestError && cause.status === 429) setError(t("mobileTooMany"));
      else if (cause instanceof IntakeRequestError && cause.code === "CHECKIN_TURNED_OFF")
        setLoad("paused");
      else if (cause instanceof IntakeRequestError) setError(cause.message);
      else setError(t("mobileOffline"));
    } finally {
      setBusy(false);
    }
  }

  async function startOver() {
    setBusy(true);
    await intake.cancel();
    setInProgress(null);
    setBusy(false);
  }

  if (load !== "ready") {
    const message =
      load === "checking"
        ? t("mobileChecking")
        : load === "paused"
          ? t("mobilePaused")
          : load === "invalid"
            ? t("mobileInvalid")
            : t("mobileOffline");
    return (
      <main className="kiosk-main mobile-checkin">
        <section className="welcome-card" aria-live="polite">
          <Smartphone aria-hidden="true" size={40} className="mobile-checkin-icon" />
          <p role={load === "checking" ? "status" : "alert"} className="welcome-intro">
            {message}
          </p>
          {load === "offline" && (
            <button
              type="button"
              className="ui-button ui-button--primary ui-button--kiosk"
              onClick={() => {
                setLoad("checking");
                void check();
              }}
            >
              {t("mobileRetry")}
            </button>
          )}
        </section>
      </main>
    );
  }

  return (
    <main className="kiosk-main mobile-checkin">
      <section className="welcome-card">
        {hospital && <p className="eyebrow">{hospital}</p>}

        {ticket && (
          <div className="mobile-ticket" role="status">
            <Ticket aria-hidden="true" size={28} />
            <div>
              <strong>{t("mobileHaveToken", { token: ticket.token })}</strong>
              <p>{t("mobileHaveTokenLead")}</p>
              {ticket.opdCard && (
                <p>
                  {t("opdCardLine", {
                    ns: "intake",
                    number: ticket.opdCard.number,
                    pin: ticket.opdCard.pin,
                  })}
                </p>
              )}
              <Link className="ui-button ui-button--primary ui-button--kiosk" to={ticket.statusUrl}>
                {t("mobileViewStatus")}
              </Link>
            </div>
          </div>
        )}

        {inProgress ? (
          <div className="mobile-resume">
            <h1>{t("mobileResumeTitle")}</h1>
            <div className="welcome-actions">
              <button
                type="button"
                className="ui-button ui-button--primary ui-button--kiosk kiosk-welcome-cta"
                disabled={busy}
                onClick={() => navigate(`/m/${code}/intake`)}
              >
                {t("mobileResume")}
              </button>
              <button
                type="button"
                className="ui-button ui-button--secondary ui-button--kiosk"
                disabled={busy}
                onClick={() => void startOver()}
              >
                {t("mobileStartOver")}
              </button>
            </div>
          </div>
        ) : (
          <>
            <h1>{ticket ? t("mobileSomeoneElse") : t("mobileWelcomeTitle")}</h1>
            <p className="welcome-intro">{t("mobileWelcomeLead")}</p>
            <p className="welcome-intro">{t("chooseLanguage")}</p>
            <LanguageSelector />
            <div className="welcome-actions">
              <button
                type="button"
                className="ui-button ui-button--primary ui-button--kiosk kiosk-welcome-cta"
                disabled={busy || networkOffline}
                onClick={() => void start()}
              >
                {busy ? "…" : t("mobileStart")}
              </button>
            </div>
            {!secureVoiceAvailable() && <p className="mobile-note">{t("mobileVoiceNote")}</p>}
          </>
        )}
        {error && (
          <p role="alert" className="mobile-error">
            {error}
          </p>
        )}
      </section>
    </main>
  );
}
