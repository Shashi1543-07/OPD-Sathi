import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { BellRing, Check, Clock3, MapPin, UserRoundSearch, Users } from "lucide-react";
import type { Language } from "../i18n";
import { useLanguageStore } from "../languageStore";
import { apiOrigin } from "../lib/api";

type Status = {
  /** "skipped": called but not there; the patient goes to the desk to be recalled. */
  state: "waiting" | "called" | "in_consult" | "skipped" | "done" | "no_show" | "cancelled";
  token: string;
  position: number | null;
  eta_min: number;
  eta_max: number;
  department?: Partial<Record<Language, string>> | null;
  hospital?: string | null;
  language?: Language;
  /** The patient's online AI help for this visit; "on" can be stopped from this page. */
  ai_help?: "on" | "off" | "withdrawn";
  /** Queue messages by SMS, which the patient ticked for themselves; "on" can be stopped. */
  sms?: "on" | "off" | "stopped";
  updated_at?: string;
};

const STEPS = ["waiting", "called", "in_consult", "done"] as const;
const LANGUAGES: { code: Language; label: string }[] = [
  { code: "hi", label: "हिन्दी" },
  { code: "mr", label: "मराठी" },
  { code: "en", label: "English" },
];
const DEMO: Status = {
  state: "waiting",
  token: "DEMO-024",
  position: 3,
  eta_min: 15,
  eta_max: 25,
  department: { en: "General Medicine", hi: "सामान्य चिकित्सा", mr: "सामान्य औषधोपचार" },
  hospital: null,
};

/**
 * Lets the patient stop something they agreed to for this visit: online AI help or queue
 * messages by SMS. It never turns either on, so a photographed token slip cannot.
 */
function StopControl({
  token,
  kind,
  onStopped,
}: {
  token: string;
  kind: "ai" | "sms";
  onStopped: () => void;
}) {
  const { t } = useTranslation("status");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const path = kind === "ai" ? "ai-consent/withdraw" : "sms/stop";

  async function stop() {
    setBusy(true);
    setFailed(false);
    try {
      const response = await fetch(`${apiOrigin}/status/${encodeURIComponent(token)}/${path}`, {
        method: "POST",
      });
      // 409: already stopped (perhaps at the desk); either way it is off now.
      if (!response.ok && response.status !== 409) throw new Error("not stopped");
      onStopped();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <section className="status-ai" aria-label={t(`${kind}Stop`)}>
      <p>{t(`${kind}On`)}</p>
      {confirming ? (
        <div className="status-ai-confirm">
          <p>{t(`${kind}StopConfirm`)}</p>
          <div className="status-ai-actions">
            <button
              type="button"
              className="status-ai-button is-stop"
              disabled={busy}
              onClick={() => void stop()}
            >
              {t(`${kind}StopYes`)}
            </button>
            <button
              type="button"
              className="status-ai-button"
              disabled={busy}
              onClick={() => setConfirming(false)}
            >
              {t(`${kind}StopNo`)}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="status-ai-button" onClick={() => setConfirming(true)}>
          {t(`${kind}Stop`)}
        </button>
      )}
      {failed && (
        <p className="status-ai-error" role="alert">
          {t(`${kind}StopFailed`)}
        </p>
      )}
    </section>
  );
}

/** The patient's phone after scanning the token QR code: live place in the queue. */
export function PatientStatus() {
  const { token = "" } = useParams<{ token: string }>();
  const { t } = useTranslation(["common", "status"]);
  const language = useLanguageStore((state) => state.language);
  const setLanguage = useLanguageStore((state) => state.setLanguage);
  const demo = token === "demo";
  const [status, setStatus] = useState<Status | undefined>(demo ? DEMO : undefined);
  const [error, setError] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(() => new Date());
  const languageChosen = useRef(false);
  const previousState = useRef<Status["state"]>();

  useEffect(() => {
    if (demo || typeof EventSource === "undefined") return;
    let disposed = false;
    let source: EventSource | undefined;
    let reconnectTimer: number | undefined;
    let backoff = 1000;
    const accept = (data: Status) => {
      if (disposed) return;
      setStatus(data);
      setUpdatedAt(new Date());
      setError(false);
    };
    const refresh = async () => {
      try {
        const response = await fetch(`${apiOrigin}/status/${encodeURIComponent(token)}`);
        if (!response.ok) throw new Error("status unavailable");
        accept((await response.json()) as Status);
      } catch {
        if (!disposed) setError(true);
      }
    };
    const connect = () => {
      if (disposed) return;
      source = new EventSource(`${apiOrigin}/status/${encodeURIComponent(token)}/stream`);
      source.addEventListener("status", (event) => {
        try {
          accept(JSON.parse((event as MessageEvent<string>).data) as Status);
          backoff = 1000;
        } catch {
          void refresh();
        }
      });
      source.onerror = () => {
        source?.close();
        reconnectTimer = window.setTimeout(connect, backoff);
        backoff = Math.min(15000, backoff * 2);
      };
    };
    void refresh();
    connect();
    const pollingTimer = window.setInterval(() => void refresh(), 5000);
    return () => {
      disposed = true;
      source?.close();
      window.clearTimeout(reconnectTimer);
      window.clearInterval(pollingTimer);
    };
  }, [demo, token]);

  // Show the page in the language the patient used at the kiosk, until they pick one.
  useEffect(() => {
    if (!languageChosen.current && status?.language && status.language !== language) {
      setLanguage(status.language);
    }
  }, [status?.language, language, setLanguage]);

  // Being called is the one moment that must not be missed: buzz and flag the tab.
  useEffect(() => {
    const state = status?.state;
    if (state === "called" && previousState.current !== "called") {
      navigator.vibrate?.([400, 150, 400, 150, 400]);
      document.title = `🔔 ${t("calledTitle", { ns: "status" })}`;
    } else if (state && state !== "called") {
      document.title = t("appName", { ns: "common" });
    }
    previousState.current = state;
  }, [status?.state, t]);

  const department = status?.department?.[language] ?? status?.department?.en ?? null;
  const step = status ? STEPS.indexOf(status.state as (typeof STEPS)[number]) : -1;
  const time = updatedAt.toLocaleTimeString(language === "en" ? "en-IN" : `${language}-IN`, {
    hour: "numeric",
    minute: "2-digit",
  });

  return (
    <main className="status-page">
      <article className="status-card" aria-live="polite">
        <header className="status-head">
          <div>
            <p className="eyebrow">{t("appName", { ns: "common" })}</p>
            {status?.hospital && <p className="status-hospital">{status.hospital}</p>}
          </div>
          <div className="status-lang" role="group" aria-label={t("language", { ns: "status" })}>
            {LANGUAGES.map((item) => (
              <button
                type="button"
                key={item.code}
                lang={item.code}
                aria-pressed={language === item.code}
                onClick={() => {
                  languageChosen.current = true;
                  setLanguage(item.code);
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </header>
        {demo && <span className="preview-label">{t("demoNotice", { ns: "common" })}</span>}
        <h1>{t("queueStatus", { ns: "status" })}</h1>

        {!status ? (
          <p role="status">
            {error ? t("statusUnavailable", { ns: "status" }) : t("loading", { ns: "status" })}
          </p>
        ) : (
          <>
            <section className="status-ticket">
              <span>{t("tokenLabel", { ns: "status" })}</span>
              <strong className="status-token">{status.token}</strong>
              {department && (
                <p className="status-go">
                  <MapPin aria-hidden="true" size={20} />
                  {t("goTo", { ns: "status" })}: <b>{department}</b>
                </p>
              )}
            </section>

            {status.state === "called" ? (
              <section className="status-called" role="alert">
                <BellRing aria-hidden="true" size={34} />
                <strong>{t("calledTitle", { ns: "status" })}</strong>
                <p>
                  {department
                    ? t("calledBody", { ns: "status", department })
                    : t("calledBodyDesk", { ns: "status" })}
                </p>
              </section>
            ) : status.state === "skipped" ? (
              <section className="status-called status-missed" role="alert">
                <UserRoundSearch aria-hidden="true" size={34} />
                <strong>{t("skippedTitle", { ns: "status" })}</strong>
                <p>{t("state.skipped", { ns: "status" })}</p>
                <p>{t("skippedBody", { ns: "status" })}</p>
              </section>
            ) : status.state === "waiting" && status.position !== null ? (
              <section className="status-figures">
                <div className="status-figure">
                  <Users aria-hidden="true" size={22} />
                  {status.position <= 1 ? (
                    <strong className="status-next">{t("youAreNext", { ns: "status" })}</strong>
                  ) : (
                    <>
                      <span>{t("peopleAhead", { ns: "status" })}</span>
                      <strong>{status.position - 1}</strong>
                    </>
                  )}
                </div>
                <div className="status-figure">
                  <Clock3 aria-hidden="true" size={22} />
                  <span>{t("estimatedWait", { ns: "status" })}</span>
                  <strong>
                    {t("minutes", {
                      ns: "status",
                      range:
                        status.eta_min === status.eta_max
                          ? `~${status.eta_max}`
                          : `${status.eta_min}–${status.eta_max}`,
                    })}
                  </strong>
                </div>
              </section>
            ) : (
              <p className="status-message" role="status">
                {t(`state.${status.state}`, { ns: "status" })}
              </p>
            )}

            {step >= 0 && (
              <ol className="status-steps">
                {STEPS.map((item, index) => (
                  <li
                    key={item}
                    className={index < step ? "is-done" : index === step ? "is-current" : ""}
                    aria-current={index === step ? "step" : undefined}
                  >
                    <span className="status-step-dot">
                      {index < step ? <Check aria-hidden="true" size={14} /> : index + 1}
                    </span>
                    <span>{t(`steps.${item}`, { ns: "status" })}</span>
                  </li>
                ))}
              </ol>
            )}

            <p className="status-updated">
              <span className={`status-live ${error ? "is-off" : ""}`} aria-hidden="true" />
              {error
                ? t("reconnecting", { ns: "status" })
                : `${t("live", { ns: "status" })} · ${t("lastUpdated", { ns: "status", time })}`}
            </p>
            {(status.state === "waiting" ||
              status.state === "called" ||
              status.state === "skipped") && (
              <p className="status-tip">{t("keepOpen", { ns: "status" })}</p>
            )}
            {!demo && status.ai_help === "on" && (
              <StopControl
                token={token}
                kind="ai"
                onStopped={() =>
                  setStatus((current) => (current ? { ...current, ai_help: "withdrawn" } : current))
                }
              />
            )}
            {status.ai_help === "withdrawn" && (
              <p className="status-tip">{t("aiStopped", { ns: "status" })}</p>
            )}
            {!demo && status.sms === "on" && (
              <StopControl
                token={token}
                kind="sms"
                onStopped={() =>
                  setStatus((current) => (current ? { ...current, sms: "stopped" } : current))
                }
              />
            )}
            {status.sms === "stopped" && (
              <p className="status-tip">{t("smsStopped", { ns: "status" })}</p>
            )}
          </>
        )}
        <p className="status-foot">{t("historyOnly", { ns: "common" })}</p>
      </article>
    </main>
  );
}
