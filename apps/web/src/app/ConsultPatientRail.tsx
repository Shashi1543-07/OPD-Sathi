import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ChevronDown, Clock3, RefreshCw } from "lucide-react";
import { waitMinutes } from "./queueBoardModel";
import { useConsultQueue, type ConsultQueue } from "./consultQueue";

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
  const queue = useConsultQueue(activeVisitId);
  return <ConsultPatientRailView activeVisitId={activeVisitId} queue={queue} />;
}

/**
 * The list itself. On narrow screens it folds into a drawer that opens by default only while
 * no patient is open; toggling it never touches the open case sheet.
 */
export function ConsultPatientRailView({
  activeVisitId,
  queue,
}: {
  activeVisitId: string | null;
  queue: ConsultQueue;
}) {
  const { t } = useTranslation("staff");
  const { items, state, now, reload } = queue;
  const bodyId = useId();
  const [open, setOpen] = useState(!activeVisitId);
  const [openFor, setOpenFor] = useState(activeVisitId);
  if (openFor !== activeVisitId) {
    // A patient was picked (or closed): fold the drawer so the case sheet is in view.
    setOpenFor(activeVisitId);
    setOpen(!activeVisitId);
  }

  return (
    <nav className={`cx-rail no-print${open ? " is-open" : ""}`} aria-label={t("todaysPatients")}>
      <div className="cx-rail-head">
        <div>
          <h3>{t("todaysPatients")}</h3>
          {state === "ready" && <span>{t("patientsWaiting", { count: items.length })}</span>}
        </div>
        <div className="cx-rail-tools">
          <button
            type="button"
            className="cx-icon-button"
            aria-label={t("refreshList")}
            title={t("refreshList")}
            onClick={reload}
          >
            <RefreshCw aria-hidden="true" size={16} />
          </button>
          <button
            type="button"
            className="cx-rail-toggle"
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? t("hidePatientList") : t("showPatientList")}
            <ChevronDown aria-hidden="true" size={16} />
          </button>
        </div>
      </div>
      <div className="cx-rail-body" id={bodyId}>
        {state === "loading" && <p className="cx-rail-note">{t("loadingQueue")}</p>}
        {state === "error" && <p className="cx-rail-note">{t("railError")}</p>}
        {state === "ready" && items.length === 0 && (
          <p className="cx-rail-note">{t("railEmpty")}</p>
        )}
        <ul>
          {items.map((item) => {
            const active = item.visit_id === activeVisitId;
            const wait = formatWait(waitMinutes(item.queued_at, now));
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
                  <span className="cx-rail-meta">
                    {item.age_years === null ? "—" : t("yearsShort", { age: item.age_years })}
                    {" · "}
                    {t(`sex_${item.sex || "unknown"}`)}
                  </span>
                  <span className="cx-rail-complaint">
                    {sentenceCase(item.complaint) || t("complaintMissing")}
                  </span>
                  {(item.urgency !== "routine" || wait) && (
                    <span className="cx-rail-foot">
                      {item.urgency !== "routine" && (
                        <span className={`cx-dot cx-dot--${item.urgency}`}>
                          <AlertTriangle aria-hidden="true" size={12} />
                          {t(`urgency_${item.urgency}`)}
                        </span>
                      )}
                      {wait && (
                        <span className="cx-rail-wait">
                          <Clock3 aria-hidden="true" size={12} />
                          {wait}
                        </span>
                      )}
                    </span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
        <Link className="cx-rail-footer" to="/staff/queue">
          {t("openFullQueue")}
        </Link>
      </div>
    </nav>
  );
}
