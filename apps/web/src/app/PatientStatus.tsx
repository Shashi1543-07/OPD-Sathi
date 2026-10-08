import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { ProgressDots, UrgencyBadge } from "../components/ui";

type Status = {
  state: "waiting" | "called" | "in_consult" | "done" | "no_show" | "cancelled";
  token: string;
  position: number | null;
  eta_min: number;
  eta_max: number;
};

const apiOrigin = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function PatientStatus() {
  const { token = "" } = useParams<{ token: string }>();
  const { t } = useTranslation(["common", "status"]);
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState(false);

  useEffect(() => {
    if (token === "demo" || typeof EventSource === "undefined") return;
    let disposed = false;
    let source: EventSource | undefined;
    let reconnectTimer: number | undefined;
    let pollingTimer: number | undefined;
    let backoff = 1000;
    const refresh = async () => {
      try {
        const response = await fetch(`${apiOrigin}/status/${encodeURIComponent(token)}`);
        if (!response.ok) throw new Error("status unavailable");
        const data = (await response.json()) as Status;
        if (!disposed) {
          setStatus(data);
          setError(false);
        }
      } catch {
        if (!disposed) setError(true);
      }
    };
    const connect = () => {
      if (disposed) return;
      source = new EventSource(`${apiOrigin}/status/${encodeURIComponent(token)}/stream`);
      source.addEventListener("status", (event) => {
        try {
          setStatus(JSON.parse((event as MessageEvent<string>).data) as Status);
          setError(false);
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
    pollingTimer = window.setInterval(() => void refresh(), 5000);
    return () => {
      disposed = true;
      source?.close();
      window.clearTimeout(reconnectTimer);
      window.clearInterval(pollingTimer);
    };
  }, [token]);

  if (token === "demo") {
    return (
      <main className="status-page">
        <article className="status-card">
          <p className="eyebrow">{t("appName", { ns: "common" })}</p>
          <span className="preview-label">{t("demoNotice", { ns: "common" })}</span>
          <h1>{t("queueStatus", { ns: "status" })}</h1>
          <p>{t("tokenLabel", { ns: "status" })}</p>
          <strong className="status-token">DEMO-024</strong>
          <div className="status-divider" />
          <div className="status-number"><span>{t("peopleAhead", { ns: "status" })}</span><strong>2</strong></div>
          <div className="status-number"><span>{t("estimatedWait", { ns: "status" })}</span><strong>~20 min</strong></div>
          <UrgencyBadge level="routine" />
          <ProgressDots current={2} total={4} />
          <p className="status-updated">{t("statusUpdated", { ns: "status" })}</p>
          <p>{t("historyOnly", { ns: "common" })}</p>
        </article>
      </main>
    );
  }

  const steps = ["waiting", "called", "in_consult", "done"] as const;
  const progressIndex = status ? Math.max(0, steps.indexOf(status.state as (typeof steps)[number])) : 0;
  return (
    <main className="status-page">
      <article className="status-card">
        <p className="eyebrow">{t("appName", { ns: "common" })}</p>
        <h1>{t("queueStatus", { ns: "status" })}</h1>
        {status ? (
          <>
            <p>{t("tokenLabel", { ns: "status" })}</p>
            <strong className="status-token">{status.token}</strong>
            {status.state === "waiting" && status.position !== null ? (
              <>
                <div className="status-divider" />
                <div className="status-number"><span>{t("peopleAhead", { ns: "status" })}</span><strong>{Math.max(0, status.position - 1)}</strong></div>
                <div className="status-number"><span>{t("estimatedWait", { ns: "status" })}</span><strong>{status.eta_min === status.eta_max ? `~${status.eta_max}` : `${status.eta_min}–${status.eta_max}`} min</strong></div>
              </>
            ) : <p role="status">{t(`state.${status.state}`, { ns: "status" })}</p>}
            <ProgressDots current={progressIndex + 1} total={steps.length} />
            <p className="status-updated">{error ? t("reconnecting", { ns: "status" }) : t("statusUpdated", { ns: "status" })}</p>
          </>
        ) : (
          <p role="status">{error ? t("statusUnavailable", { ns: "status" }) : t("loading", { ns: "status" })}</p>
        )}
        <p>{t("historyOnly", { ns: "common" })}</p>
      </article>
    </main>
  );
}
