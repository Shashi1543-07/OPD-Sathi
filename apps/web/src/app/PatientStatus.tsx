import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { BellRing, Check, Clock3, MapPin, Users } from "lucide-react";
import type { Language } from "../i18n";
import { useLanguageStore } from "../languageStore";
import { apiOrigin } from "../lib/api";

type Status = {
  state: "waiting" | "called" | "in_consult" | "done" | "no_show" | "cancelled";
  token: string;
  position: number | null;
  eta_min: number;
  eta_max: number;
  department?: Partial<Record<Language, string>> | null;
  hospital?: string | null;
  language?: Language;
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
            {(status.state === "waiting" || status.state === "called") && (
              <p className="status-tip">{t("keepOpen", { ns: "status" })}</p>
            )}
          </>
        )}
        <p className="status-foot">{t("historyOnly", { ns: "common" })}</p>
      </article>
    </main>
  );
}
