import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { FileText, RefreshCw, Search, UserPlus, X } from "lucide-react";
import type { Language } from "../i18n";
import { KioskIntake } from "./KioskIntake";
import { intake } from "../lib/intake";
import { staffAuth } from "../lib/auth";
import { UrgencyBadge } from "../components/ui";

type VisitStatus =
  | "registering"
  | "awaiting_confirmation"
  | "queued"
  | "called"
  | "in_consult"
  | "done"
  | "cancelled"
  | "no_show";

type PatientVisit = {
  visit_id: string;
  token: string | null;
  status: VisitStatus;
  urgency: "routine" | "priority" | "urgent";
  age_years: number | null;
  sex: string;
  language?: string | null;
  department: string;
  registered_at: string;
  complaint: string;
  case_sheet_available?: boolean;
};

type Period = "1" | "7" | "30";
type StatusGroup = "all" | "waiting" | "with_doctor" | "done" | "incomplete" | "closed";
type UrgencyFilter = "any" | "flagged" | "urgent";

const STATUS_GROUPS: Record<Exclude<StatusGroup, "all">, VisitStatus[]> = {
  waiting: ["queued", "called"],
  with_doctor: ["in_consult"],
  done: ["done"],
  incomplete: ["registering", "awaiting_confirmation"],
  closed: ["cancelled", "no_show"],
};
const CASE_SHEET_STATUSES: VisitStatus[] = ["queued", "called", "in_consult", "done"];

async function loadPatientVisits(days: Period): Promise<PatientVisit[]> {
  const response = await staffAuth.request(`/patients?limit=200&days=${days}`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error("PATIENT_LIST_UNAVAILABLE");
  const result = (await response.json()) as { items: PatientVisit[] };
  return result.items;
}

function sentenceCase(text: string) {
  const value = text.trim();
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function groupOf(status: VisitStatus): Exclude<StatusGroup, "all"> {
  for (const [group, statuses] of Object.entries(STATUS_GROUPS)) {
    if (statuses.includes(status)) return group as Exclude<StatusGroup, "all">;
  }
  return "closed";
}

export function NurseConsole() {
  const { t, i18n } = useTranslation("staff");
  const [language, setLanguage] = useState<Language>("en");
  const [started, setStarted] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [period, setPeriod] = useState<Period>("1");
  const [statusGroup, setStatusGroup] = useState<StatusGroup>("all");
  const [urgency, setUrgency] = useState<UrgencyFilter>("any");
  const [department, setDepartment] = useState("all");
  const role = staffAuth.user?.role;
  const canOpenCase = role === "doctor" || role === "admin";
  const patientsQuery = useQuery({
    queryKey: ["staff", "patient-visits", period],
    queryFn: () => loadPatientVisits(period),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const visits = useMemo(() => patientsQuery.data ?? [], [patientsQuery.data]);
  const departments = useMemo(
    () => [...new Set(visits.map((visit) => visit.department))].sort(),
    [visits],
  );
  const counts = useMemo(() => {
    const result: Record<StatusGroup, number> = {
      all: visits.length,
      waiting: 0,
      with_doctor: 0,
      done: 0,
      incomplete: 0,
      closed: 0,
    };
    for (const visit of visits) result[groupOf(visit.status)] += 1;
    result.all = result.waiting + result.with_doctor + result.done;
    return result;
  }, [visits]);
  const filteredVisits = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return visits.filter((visit) => {
      const group = groupOf(visit.status);
      // "Checked in" hides abandoned and cancelled check-ins; their own tiles show them.
      if (statusGroup === "all" && (group === "incomplete" || group === "closed")) return false;
      if (statusGroup !== "all" && group !== statusGroup) return false;
      if (urgency === "flagged" && visit.urgency === "routine") return false;
      if (urgency === "urgent" && visit.urgency !== "urgent") return false;
      if (department !== "all" && visit.department !== department) return false;
      if (!needle) return true;
      return [visit.token, visit.complaint, visit.department]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [search, visits, statusGroup, urgency, department]);
  const filtersActive =
    Boolean(search) || urgency !== "any" || department !== "all";

  const timeFormat = useMemo(
    () =>
      new Intl.DateTimeFormat(i18n.language, {
        hour: "numeric",
        minute: "2-digit",
        timeZone: "Asia/Kolkata",
      }),
    [i18n.language],
  );
  const dayFormat = useMemo(
    () =>
      new Intl.DateTimeFormat(i18n.language, {
        day: "numeric",
        month: "short",
        timeZone: "Asia/Kolkata",
      }),
    [i18n.language],
  );
  const todayLabel = dayFormat.format(new Date());

  if (started) {
    return (
      <section className="nurse-console nurse-console--intake">
        <header>
          <h2>{t("assistedRegistration")}</h2>
          <Link to="/staff/queue">{t("backToQueue")}</Link>
        </header>
        <KioskIntake />
      </section>
    );
  }

  async function beginRegistration() {
    setWorking(true);
    setError("");
    try {
      await intake.startAssisted(language);
      setStarted(true);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : t("assistedStartError"),
      );
    } finally {
      setWorking(false);
    }
  }

  function clearFilters() {
    setSearch("");
    setStatusGroup("all");
    setUrgency("any");
    setDepartment("all");
  }

  const tiles: { group: StatusGroup; label: string; tone: string }[] = [
    { group: "all", label: t("tileAll"), tone: "all" },
    { group: "waiting", label: t("tileWaiting"), tone: "wait" },
    { group: "with_doctor", label: t("tileWithDoctor"), tone: "consult" },
    { group: "done", label: t("tileDone"), tone: "done" },
    { group: "incomplete", label: t("tileIncomplete"), tone: "incomplete" },
    { group: "closed", label: t("tileClosed"), tone: "closed" },
  ];

  return (
    <section className="nurse-console pr">
      <header className="pr-head">
        <div>
          <h2>{t("patients")}</h2>
          <p>
            {role === "nurse"
              ? t("nurseConsoleDescription")
              : t("patientRegisterDescription")}
          </p>
        </div>
        <div className="pr-head-actions">
          <Link className="pr-btn" to="/staff/queue">
            {t("reviewAlertsQueue")}
          </Link>
          <button
            type="button"
            className="pr-btn"
            onClick={() => void patientsQuery.refetch()}
            disabled={patientsQuery.isFetching}
          >
            <RefreshCw aria-hidden="true" size={15} />
            {patientsQuery.isFetching
              ? t("refreshingPatients")
              : t("refreshPatients")}
          </button>
        </div>
      </header>

      {role === "nurse" && (
        <article className="pr-register">
          <UserPlus aria-hidden="true" size={26} />
          <div className="pr-register-text">
            <h3>{t("assistedRegistration")}</h3>
            <p>{t("consentBeforeRegistration")}</p>
          </div>
          <label className="pr-field">
            <span>{t("preferredLanguage")}</span>
            <select
              id="assisted-language"
              value={language}
              onChange={(event) => setLanguage(event.target.value as Language)}
            >
              <option value="en">{t("languageEnglish")}</option>
              <option value="hi">{t("languageHindi")}</option>
              <option value="mr">{t("languageMarathi")}</option>
            </select>
          </label>
          <button
            type="button"
            className="pr-btn pr-btn--primary"
            disabled={working}
            onClick={() => void beginRegistration()}
          >
            {working ? t("startingRegistration") : t("beginRegistration")}
          </button>
          {error && <p className="pr-error" role="alert">{error}</p>}
        </article>
      )}

      <div className="pr-tiles" role="group" aria-label={t("filterByStatus")}>
        {tiles.map((tile) => (
          <button
            type="button"
            key={tile.group}
            className={`pr-tile pr-tile--${tile.tone}`}
            aria-pressed={statusGroup === tile.group}
            onClick={() => setStatusGroup(tile.group)}
          >
            <strong>{patientsQuery.isSuccess ? counts[tile.group] : "–"}</strong>
            <span>{tile.label}</span>
          </button>
        ))}
      </div>

      <section className="pr-register-list" aria-labelledby="patient-register-title">
        <div className="pr-toolbar">
          <div className="pr-title">
            <h3 id="patient-register-title">{t("recentVisits")}</h3>
            <p>
              {t("showingVisits", { shown: filteredVisits.length, total: visits.length })}
            </p>
          </div>
          <div className="pr-filters">
            <label className="pr-search">
              <span className="cx-visually-hidden">{t("searchPatients")}</span>
              <Search aria-hidden="true" size={16} />
              <input
                type="search"
                value={search}
                aria-label={t("searchPatients")}
                onChange={(event) => setSearch(event.target.value)}
                placeholder={t("searchPatientsPlaceholder")}
              />
            </label>
            <label className="pr-field">
              <span>{t("filterPeriod")}</span>
              <select
                value={period}
                onChange={(event) => setPeriod(event.target.value as Period)}
              >
                <option value="1">{t("periodToday")}</option>
                <option value="7">{t("periodWeek")}</option>
                <option value="30">{t("periodMonth")}</option>
              </select>
            </label>
            <label className="pr-field">
              <span>{t("filterUrgency")}</span>
              <select
                value={urgency}
                onChange={(event) => setUrgency(event.target.value as UrgencyFilter)}
              >
                <option value="any">{t("urgencyAny")}</option>
                <option value="flagged">{t("urgencyFlagged")}</option>
                <option value="urgent">{t("urgency_urgent")}</option>
              </select>
            </label>
            {departments.length > 1 && (
              <label className="pr-field">
                <span>{t("department")}</span>
                <select
                  value={department}
                  onChange={(event) => setDepartment(event.target.value)}
                >
                  <option value="all">{t("allDepartments")}</option>
                  {departments.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {filtersActive && (
              <button type="button" className="pr-btn pr-btn--ghost" onClick={clearFilters}>
                <X aria-hidden="true" size={15} /> {t("clearFilters")}
              </button>
            )}
          </div>
        </div>

        {patientsQuery.isPending ? (
          <p className="pr-empty" role="status">{t("loadingPatients")}</p>
        ) : null}
        {patientsQuery.isError ? (
          <div className="pr-empty" role="alert">
            <p>{t("patientsLoadError")}</p>
            <button
              type="button"
              className="pr-btn"
              onClick={() => void patientsQuery.refetch()}
            >
              {t("retry")}
            </button>
          </div>
        ) : null}
        {patientsQuery.isSuccess && filteredVisits.length === 0 ? (
          <div className="pr-empty">
            <strong>
              {filtersActive ? t("noPatientMatches") : t("noPatientVisits")}
            </strong>
            {filtersActive ? (
              <button type="button" className="pr-btn" onClick={clearFilters}>
                {t("clearFilters")}
              </button>
            ) : (
              <p>{t("seedDemoHint")}</p>
            )}
          </div>
        ) : null}
        {filteredVisits.length > 0 && (
          <div className="pr-table-wrap">
            <table className="pr-table">
              <thead>
                <tr>
                  <th>{t("visitToken")}</th>
                  <th>{t("patientColumn")}</th>
                  <th>{t("chiefComplaint")}</th>
                  <th>{t("department")}</th>
                  <th>{t("urgencyColumn")}</th>
                  <th>{t("visitStatus")}</th>
                  <th>
                    <span className="cx-visually-hidden">{t("openCaseSheet")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredVisits.map((visit) => {
                  const registered = new Date(visit.registered_at);
                  const day = dayFormat.format(registered);
                  const incomplete = groupOf(visit.status) === "incomplete";
                  const sheetReady =
                    visit.case_sheet_available !== false &&
                    CASE_SHEET_STATUSES.includes(visit.status);
                  return (
                    <tr
                      key={visit.visit_id}
                      className={`pr-row pr-row--${visit.urgency}${incomplete ? " pr-row--muted" : ""}`}
                    >
                      <td data-label={t("visitToken")}>
                        <strong className="pr-token">{visit.token || "—"}</strong>
                        <small>
                          {day === todayLabel ? "" : `${day}, `}
                          {timeFormat.format(registered)}
                        </small>
                      </td>
                      <td data-label={t("patientColumn")}>
                        <span className="pr-nowrap">
                          {visit.age_years === null
                            ? "—"
                            : t("yearsShort", { age: visit.age_years })}
                          {" · "}
                          {t(`sex_${visit.sex || "unknown"}`, {
                            defaultValue: visit.sex,
                          })}
                        </span>
                        {visit.language && (
                          <small>{t(`lang_${visit.language}`)}</small>
                        )}
                      </td>
                      <td data-label={t("chiefComplaint")} className="pr-complaint">
                        {sentenceCase(visit.complaint) || (
                          <span className="pr-muted">{t("complaintMissing")}</span>
                        )}
                      </td>
                      <td data-label={t("department")}>{visit.department}</td>
                      <td data-label={t("urgencyColumn")}>
                        <UrgencyBadge level={visit.urgency} />
                      </td>
                      <td data-label={t("visitStatus")}>
                        <span className={`pr-status pr-status--${visit.status}`}>
                          {t(`status_${visit.status}`, {
                            defaultValue: visit.status.replaceAll("_", " "),
                          })}
                        </span>
                      </td>
                      <td className="pr-action">
                        {canOpenCase && sheetReady ? (
                          <Link
                            className="pr-btn"
                            to={`/staff/consultations?visit_id=${encodeURIComponent(visit.visit_id)}&status=${encodeURIComponent(visit.status)}`}
                          >
                            <FileText aria-hidden="true" size={15} />
                            {t("openCaseSheet")}
                          </Link>
                        ) : canOpenCase && incomplete ? (
                          <span className="pr-muted">{t("caseSheetNotReady")}</span>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {role === "nurse" && (
        <p className="nurse-console-note">{t("nurseQueueTools")}</p>
      )}
    </section>
  );
}
