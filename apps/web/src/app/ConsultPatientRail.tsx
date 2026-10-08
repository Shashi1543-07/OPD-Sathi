import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { staffAuth } from "../lib/auth";
import { sortQueue, waitMinutes, type QueueItem } from "./queueBoardModel";

type RailState = "loading" | "ready" | "error";

function formatWait(minutes: number | null) {
  if (minutes === null) return "";
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} m` : `${hours} h`;
}

function sentenceCase(text: string) {
  const value = text.trim();
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

/** Today's patients for the signed-in clinician, so the consultation page is never empty. */
export function ConsultPatientRail({ activeVisitId }: { activeVisitId: string | null }) {
  const { t } = useTranslation("staff");
  const [items, setItems] = useState<QueueItem[]>([]);
  const [state, setState] = useState<RailState>("loading");
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const response = await staffAuth.request("/queue?limit=50", { cache: "no-store" });
      if (!response.ok) throw new Error("queue unavailable");
      const body = (await response.json()) as { items?: QueueItem[] };
      setItems(sortQueue(body.items ?? []));
      setNow(Date.now());
      setState("ready");
    } catch {
      setState("error");
    }
  }, []);

  useEffect(() => {
    const first = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(), 30_000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [load, activeVisitId]);

  return (
    <nav className="cx-rail no-print" aria-label={t("todaysPatients")}>
      <div className="cx-rail-head">
        <div>
          <h3>{t("todaysPatients")}</h3>
          {state === "ready" && <span>{t("patientsWaiting", { count: items.length })}</span>}
        </div>
        <button
          type="button"
          className="cx-icon-button"
          aria-label={t("refreshList")}
          title={t("refreshList")}
          onClick={() => void load()}
        >
          <RefreshCw aria-hidden="true" size={16} />
        </button>
      </div>
      {state === "loading" && <p className="cx-rail-note">{t("loadingQueue")}</p>}
      {state === "error" && <p className="cx-rail-note">{t("railError")}</p>}
      {state === "ready" && items.length === 0 && (
        <p className="cx-rail-note">{t("railEmpty")}</p>
      )}
      <ul>
        {items.map((item) => {
          const active = item.visit_id === activeVisitId;
          return (
            <li key={item.visit_id}>
              <Link
                to={`/staff/consultations?visit_id=${item.visit_id}&status=${item.status}`}
                className={`cx-rail-item cx-rail-item--${item.urgency}${active ? " is-active" : ""}`}
                aria-current={active ? "page" : undefined}
              >
                <span className="cx-rail-top">
                  <strong>{item.token}</strong>
                  <span className={`cx-pill cx-pill--${item.status}`}>
                    {t(`queueStatus_${item.status}`)}
                  </span>
                </span>
                <span className="cx-rail-complaint">
                  {sentenceCase(item.complaint) || t("complaintMissing")}
                </span>
                <span className="cx-rail-meta">
                  {item.age_years === null ? "—" : t("yearsShort", { age: item.age_years })}
                  {" · "}
                  {t(`sex_${item.sex || "unknown"}`)}
                  {item.urgency !== "routine" && (
                    <span className={`cx-dot cx-dot--${item.urgency}`}>
                      {t(`urgency_${item.urgency}`)}
                    </span>
                  )}
                  <span className="cx-rail-wait">{formatWait(waitMinutes(item.queued_at, now))}</span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
      <Link className="cx-rail-footer" to="/staff/queue">
        {t("openFullQueue")}
      </Link>
    </nav>
  );
}
