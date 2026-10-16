import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import {
  AlertTriangle,
  BarChart3,
  Building2,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Download,
  FileText,
  Globe2,
  Hourglass,
  Info,
  PencilLine,
  RefreshCw,
  Settings2,
  Stethoscope,
  Users,
} from "lucide-react";
import { staffAuth } from "../lib/auth";
import { DailyTrendChart, type DailyPoint } from "./DailyTrendChart";

type DepartmentLoad = {
  department_id: string;
  name: Record<string, string>;
  patients: number;
  urgent_count: number;
  average_wait_seconds: number | null;
};
type Summary = {
  patients: number;
  average_wait_seconds: number | null;
  average_consult_seconds: number | null;
  urgent_count: number;
  language_mix: Record<string, number>;
  average_interview_seconds: number | null;
  confirmed_without_edits_percent: number | null;
  doctor_edit_rate_percent: number | null;
  department_load: DepartmentLoad[];
  start_day?: string;
  end_day?: string;
};
type TrendItem = { service_date: string; patient_count: number };
type Trend = { days: number; items: TrendItem[] };
type Department = { id: string; name_en: string; name_hi: string; name_mr: string };

type ReportSettings = {
  minutes_saved_per_visit: number;
  is_default: boolean;
  updated_at: string | null;
  updated_by_name: string | null;
};

const DEFAULT_BASELINE = 3;
const MAX_BASELINE = 60;
const RANGES = [1, 7, 30, 90] as const;

function minutes(seconds: number | null | undefined): number | null {
  return seconds == null ? null : seconds / 60;
}

function isoDay(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function dayRange(start: string, end: string): string[] {
  const days: string[] = [];
  const cursor = new Date(`${start}T00:00:00`);
  const last = new Date(`${end}T00:00:00`);
  while (cursor <= last && days.length < 400) {
    days.push(isoDay(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

function Kpi({
  icon,
  tone,
  label,
  value,
  unit,
  detail,
  emptyDetail,
  help,
  action,
}: {
  icon: ReactNode;
  tone: "teal" | "blue" | "violet" | "red" | "green" | "amber";
  label: string;
  value: string | null;
  unit?: string;
  detail: string;
  emptyDetail?: string;
  help?: string;
  action?: ReactNode;
}) {
  const { t } = useTranslation("staff");
  const helpId = useId();
  const [showHelp, setShowHelp] = useState(false);
  return (
    <article className={`rp-kpi rp-kpi--${tone}`}>
      <span className="rp-kpi-icon" aria-hidden="true">
        {icon}
      </span>
      <div className="rp-kpi-body">
        <h3>
          {label}
          {help && (
            <button
              type="button"
              className="rp-help"
              title={help}
              aria-label={t("aboutMetric", { metric: label })}
              aria-expanded={showHelp}
              aria-controls={helpId}
              onClick={() => setShowHelp((value) => !value)}
            >
              <Info aria-hidden="true" size={13} />
            </button>
          )}
        </h3>
        {value === null ? (
          <p className="rp-kpi-value rp-kpi-value--empty">{t("analyticsNoValue")}</p>
        ) : (
          <p className="rp-kpi-value">
            {value}
            {unit && <small>{unit === "%" ? unit : ` ${unit}`}</small>}
          </p>
        )}
        <p className="rp-kpi-detail">{value === null && emptyDetail ? emptyDetail : detail}</p>
        {help && showHelp && (
          <p className="rp-kpi-help" id={helpId}>
            {help}
          </p>
        )}
        {action}
      </div>
    </article>
  );
}

export function AnalyticsDashboard() {
  const { t, i18n } = useTranslation("staff");
  const language = i18n.resolvedLanguage?.split("-")[0] ?? "en";
  const [days, setDays] = useState<number>(30);
  const [departmentId, setDepartmentId] = useState("");
  const queryClient = useQueryClient();
  const [configOpen, setConfigOpen] = useState(false);
  // One estimate for the whole hospital, kept on the server and changed only by admins.
  const reportSettings = useQuery({
    queryKey: ["analytics-settings"],
    queryFn: async () => {
      const response = await staffAuth.request("/analytics/settings");
      if (!response.ok) throw new Error("Report settings unavailable");
      return (await response.json()) as ReportSettings;
    },
  });
  const baseline = reportSettings.data?.minutes_saved_per_visit ?? null;
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(false);
  const query = new URLSearchParams({ days: String(days) });
  if (departmentId) query.set("department_id", departmentId);
  const filter = query.toString();

  const departments = useQuery({
    queryKey: ["analytics-departments"],
    queryFn: async () => {
      const response = await staffAuth.request("/admin/departments?limit=100");
      if (!response.ok) return [] as Department[];
      return ((await response.json()) as { items?: Department[] }).items ?? [];
    },
  });
  const { data, isLoading, isError, isFetching, dataUpdatedAt, refetch } = useQuery({
    queryKey: ["analytics-dashboard", filter],
    queryFn: async () => {
      const [summaryResponse, trendResponse] = await Promise.all([
        staffAuth.request(`/analytics/summary?${filter}`),
        staffAuth.request(`/analytics/timeseries?${filter}`),
      ]);
      if (!summaryResponse.ok || !trendResponse.ok) throw new Error("Analytics unavailable");
      const [summary, trend] = (await Promise.all([
        summaryResponse.json(),
        trendResponse.json(),
      ])) as [Summary, Trend];
      return { summary, trend: trend.items };
    },
  });
  const summary = data?.summary;

  const dateLabel = useMemo(
    () => new Intl.DateTimeFormat(i18n.language, { day: "numeric", month: "short" }),
    [i18n.language],
  );
  const longDateLabel = useMemo(
    () =>
      new Intl.DateTimeFormat(i18n.language, {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
      }),
    [i18n.language],
  );
  const asDate = (day: string) => new Date(`${day}T00:00:00`);

  // Every day of the period, summed over departments; a day without a rollup row had no visits.
  const daily: DailyPoint[] = useMemo(() => {
    const byDate = new Map<string, number>();
    for (const item of data?.trend ?? []) {
      byDate.set(item.service_date, (byDate.get(item.service_date) ?? 0) + item.patient_count);
    }
    const end = summary?.end_day ?? isoDay(new Date());
    const start =
      summary?.start_day ?? isoDay(new Date(new Date(`${end}T00:00:00`).getTime() - (days - 1) * 864e5));
    return dayRange(start, end).map((date) => ({ date, count: byDate.get(date) ?? 0 }));
  }, [data, summary, days]);
  const trendTotal = daily.reduce((sum, point) => sum + point.count, 0);
  const languages = Object.entries(summary?.language_mix ?? {}).sort(([, a], [, b]) => b - a);
  const languageTotal = languages.reduce((sum, [, count]) => sum + count, 0);
  const departmentTotal = (summary?.department_load ?? []).reduce((sum, row) => sum + row.patients, 0);
  const periodName = days === 1 ? t("rangeToday") : t("rangeLastDays", { count: days });
  const departmentName = (() => {
    const department = departments.data?.find((item) => item.id === departmentId);
    if (!department) return t("allDepartments");
    return (
      { en: department.name_en, hi: department.name_hi, mr: department.name_mr }[language] ??
      department.name_en
    );
  })();
  const periodDates =
    summary?.start_day && summary.end_day
      ? summary.start_day === summary.end_day
        ? longDateLabel.format(asDate(summary.end_day))
        : `${longDateLabel.format(asDate(summary.start_day))} – ${longDateLabel.format(asDate(summary.end_day))}`
      : "";

  const fixed = (value: number | null, digits: number) =>
    value === null ? null : value.toLocaleString(i18n.language, { maximumFractionDigits: digits });
  const timeSaved = summary && baseline !== null ? summary.patients * baseline : null;
  const timeSavedText =
    timeSaved === null
      ? { value: null, unit: undefined }
      : timeSaved >= 120
        ? { value: fixed(timeSaved / 60, 1), unit: t("hoursUnit") }
        : { value: fixed(timeSaved, 1), unit: t("minutesUnit") };

  async function saveBaseline(value: number) {
    const response = await staffAuth.request("/analytics/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes_saved_per_visit: value }),
    });
    if (!response.ok) throw new Error("Report settings not saved");
    queryClient.setQueryData(["analytics-settings"], (await response.json()) as ReportSettings);
  }

  async function exportCsv() {
    setExporting(true);
    setExportError(false);
    try {
      const exportQuery = new URLSearchParams({ days: String(days) });
      if (departmentId) exportQuery.set("department_id", departmentId);
      const response = await staffAuth.request(`/analytics/export.csv?${exportQuery}`);
      if (!response.ok) throw new Error("Export failed");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "opd-analytics.csv";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      setExportError(true);
    } finally {
      setExporting(false);
    }
  }

  return (
    <section className="rp" aria-labelledby="analytics-title">
      <header className="rp-head">
        <div>
          <h2 id="analytics-title">{t("reports")}</h2>
          <p>{t("analyticsDescription")}</p>
        </div>
        <div className="rp-controls">
          <label className="rp-select">
            <CalendarDays aria-hidden="true" size={16} />
            <span className="cx-visually-hidden">{t("dateRange")}</span>
            <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {RANGES.map((value) => (
                <option key={value} value={value}>
                  {value === 1 ? t("rangeToday") : t("rangeLastDays", { count: value })}
                </option>
              ))}
            </select>
          </label>
          <label className="rp-select">
            <Building2 aria-hidden="true" size={16} />
            <span className="cx-visually-hidden">{t("department")}</span>
            <select value={departmentId} onChange={(event) => setDepartmentId(event.target.value)}>
              <option value="">{t("allDepartments")}</option>
              {(departments.data ?? []).map((department) => (
                <option key={department.id} value={department.id}>
                  {{ en: department.name_en, hi: department.name_hi, mr: department.name_mr }[
                    language
                  ] ?? department.name_en}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="rp-export"
            onClick={() => void exportCsv()}
            disabled={exporting || isLoading}
          >
            <Download aria-hidden="true" size={16} />
            {exporting ? t("analyticsExporting") : t("analyticsExport")}
          </button>
        </div>
      </header>
      <p className="rp-period">
        {periodName}
        {periodDates && <span> · {periodDates}</span>}
        <span> · {departmentName}</span>
      </p>

      {isError && (
        <div className="rp-error" role="alert">
          <AlertTriangle aria-hidden="true" size={16} />
          {t("analyticsError")}
          <button type="button" onClick={() => void refetch()}>
            {t("retry")}
          </button>
        </div>
      )}
      {exportError && (
        <div className="rp-error" role="alert">
          <AlertTriangle aria-hidden="true" size={16} />
          {t("analyticsExportError")}
        </div>
      )}

      {isLoading ? (
        <p className="rp-loading" role="status">
          {t("analyticsLoading")}
        </p>
      ) : (
        summary && (
          <>
            <div className="rp-kpis" aria-label={t("analyticsPrimary")} role="group">
              <Kpi
                icon={<Users size={20} />}
                tone="blue"
                label={t("analyticsPatients")}
                value={fixed(summary.patients, 0)}
                detail={t("analyticsPatientsDetail")}
                help={t("analyticsPatientsHelp")}
              />
              <Kpi
                icon={<Clock3 size={20} />}
                tone="blue"
                label={t("analyticsWait")}
                value={fixed(minutes(summary.average_wait_seconds), 1)}
                unit={t("minutesUnit")}
                detail={t("analyticsWaitDetail")}
                emptyDetail={t("analyticsWaitEmpty")}
              />
              <Kpi
                icon={<Stethoscope size={20} />}
                tone="blue"
                label={t("analyticsConsult")}
                value={fixed(minutes(summary.average_consult_seconds), 1)}
                unit={t("minutesUnit")}
                detail={t("analyticsConsultDetail")}
                emptyDetail={t("analyticsConsultEmpty")}
              />
              <Kpi
                icon={<AlertTriangle size={20} />}
                tone="red"
                label={t("analyticsUrgent")}
                value={fixed(summary.urgent_count, 0)}
                detail={t("analyticsUrgentDetail")}
              />
            </div>
            <div className="rp-kpis" aria-label={t("analyticsSupporting")} role="group">
              <Kpi
                icon={<CheckCircle2 size={20} />}
                tone="green"
                label={t("analyticsConfirmed")}
                value={fixed(summary.confirmed_without_edits_percent, 1)}
                unit="%"
                detail={t("analyticsConfirmedDetail")}
                emptyDetail={t("analyticsConfirmedEmpty")}
              />
              <Kpi
                icon={<PencilLine size={20} />}
                tone="violet"
                label={t("analyticsDoctorEditRate")}
                value={fixed(summary.doctor_edit_rate_percent, 1)}
                unit="%"
                detail={t("analyticsDoctorEditDetail")}
                emptyDetail={t("analyticsNoSheetsEmpty")}
                help={t("analyticsDoctorEditHelp")}
              />
              <Kpi
                icon={<FileText size={20} />}
                tone="blue"
                label={t("analyticsInterview")}
                value={fixed(minutes(summary.average_interview_seconds), 1)}
                unit={t("minutesUnit")}
                detail={t("analyticsInterviewDetail")}
                emptyDetail={t("analyticsInterviewEmpty")}
              />
              <Kpi
                icon={<Hourglass size={20} />}
                tone="amber"
                label={t("analyticsTimeSaved")}
                value={timeSavedText.value}
                unit={timeSavedText.unit}
                detail={t("analyticsTimeSavedDetail", {
                  minutes: fixed(baseline, 1),
                  count: summary.patients,
                })}
                emptyDetail={
                  reportSettings.isError ? t("analyticsSettingsError") : t("analyticsLoading")
                }
                help={t("analyticsTimeSavedHelp")}
                action={
                  <button
                    type="button"
                    className="rp-configure"
                    disabled={!reportSettings.data}
                    onClick={() => setConfigOpen(true)}
                  >
                    <Settings2 aria-hidden="true" size={14} />
                    {t("configureMetric")}
                  </button>
                }
              />
            </div>

            <div className="rp-panels">
              <article className="rp-card rp-trend">
                <div className="rp-card-head">
                  <h3>
                    <BarChart3 aria-hidden="true" size={18} />
                    {t("analyticsTrend")}
                  </h3>
                  <span className="rp-card-meta">
                    {t("analyticsTrendTotal", { count: trendTotal })} · {periodName}
                  </span>
                </div>
                {trendTotal === 0 ? (
                  <p className="rp-empty">{t("analyticsNoTrend")}</p>
                ) : (
                  <DailyTrendChart
                    points={daily}
                    label={t("analyticsTrendAccessible", { period: periodName, count: trendTotal })}
                    axisLabel={t("analyticsVisitsAxis")}
                    formatDay={(day) => dateLabel.format(asDate(day))}
                    formatDayLong={(day) => longDateLabel.format(asDate(day))}
                    unitLabel={(count) => t("analyticsVisitCount", { count })}
                  />
                )}
                <p className="rp-caption">{t("analyticsTrendCaption")}</p>
              </article>
              <article className="rp-card rp-languages">
                <div className="rp-card-head">
                  <h3>
                    <Globe2 aria-hidden="true" size={18} />
                    {t("analyticsLanguages")}
                  </h3>
                  <span className="rp-card-meta">{periodName}</span>
                </div>
                {languages.length ? (
                  <ul className="rp-lang-list">
                    {languages.map(([code, count]) => {
                      const share = languageTotal ? (count / languageTotal) * 100 : 0;
                      return (
                        <li key={code}>
                          <span className="rp-lang-name">
                            {t(`language_${code}`, { defaultValue: code.toUpperCase() })}
                          </span>
                          <strong>{count.toLocaleString(i18n.language)}</strong>
                          <span className="rp-lang-bar" aria-hidden="true">
                            <span style={{ width: `${share}%` }} />
                          </span>
                          <span className="rp-lang-share">
                            {share.toLocaleString(i18n.language, { maximumFractionDigits: 1 })}%
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="rp-empty">{t("analyticsNoData")}</p>
                )}
              </article>
            </div>

            <article className="rp-card rp-departments">
              <div className="rp-card-head">
                <h3>
                  <Building2 aria-hidden="true" size={18} />
                  {t("analyticsDepartmentLoad")}
                </h3>
                <span className="rp-card-meta">{periodName}</span>
              </div>
              {summary.department_load.length ? (
                <div className="rp-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">{t("department")}</th>
                        <th scope="col">{t("analyticsPatients")}</th>
                        <th scope="col">{t("analyticsUrgent")}</th>
                        <th scope="col">{t("analyticsWait")}</th>
                        <th scope="col">{t("analyticsShare")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.department_load.map((row) => {
                        const wait = fixed(minutes(row.average_wait_seconds), 1);
                        return (
                          <tr key={row.department_id}>
                            <th scope="row">{row.name[language] ?? row.name.en}</th>
                            <td>{row.patients.toLocaleString(i18n.language)}</td>
                            <td>{row.urgent_count.toLocaleString(i18n.language)}</td>
                            <td>{wait === null ? t("analyticsNoValue") : `${wait} ${t("minutesUnit")}`}</td>
                            <td>
                              {fixed(departmentTotal ? (row.patients / departmentTotal) * 100 : 0, 1)}%
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="rp-empty">{t("analyticsNoData")}</p>
              )}
            </article>
          </>
        )
      )}

      <footer className="rp-about">
        <Info aria-hidden="true" size={18} />
        <div>
          <strong>{t("analyticsAboutTitle")}</strong>
          <p>{t("analyticsAboutText")}</p>
        </div>
        <div className="rp-updated">
          <span>
            {dataUpdatedAt
              ? t("analyticsLoadedAt", {
                  time: new Intl.DateTimeFormat(i18n.language, {
                    hour: "2-digit",
                    minute: "2-digit",
                  }).format(dataUpdatedAt),
                })
              : ""}
          </span>
          <button
            type="button"
            className="rp-refresh"
            onClick={() => void refetch()}
            disabled={isFetching}
          >
            <RefreshCw aria-hidden="true" size={14} />
            {t("refreshData")}
          </button>
        </div>
      </footer>

      {configOpen && reportSettings.data && (
        <BaselineDialog
          settings={reportSettings.data}
          onCancel={() => setConfigOpen(false)}
          onSave={async (value) => {
            await saveBaseline(value);
            setConfigOpen(false);
          }}
        />
      )}
    </section>
  );
}

function BaselineDialog({
  settings,
  onCancel,
  onSave,
}: {
  settings: ReportSettings;
  onCancel: () => void;
  onSave: (value: number) => Promise<void>;
}) {
  const { t, i18n } = useTranslation("staff");
  const id = useId();
  const [draft, setDraft] = useState(String(settings.minutes_saved_per_visit));
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const number = Number(draft);
  const valid = draft.trim() !== "" && Number.isFinite(number) && number >= 0 && number <= MAX_BASELINE;

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  return (
    <div
      className="rp-dialog-backdrop"
      onKeyDown={(event) => {
        if (event.key === "Escape") onCancel();
      }}
    >
      <form
        className="rp-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        onSubmit={(event) => {
          event.preventDefault();
          if (!valid || saving) return;
          setSaving(true);
          setFailed(false);
          onSave(number).catch(() => {
            setFailed(true);
            setSaving(false);
          });
        }}
      >
        <h3 id={`${id}-title`}>{t("configureTimeSaved")}</h3>
        <p>{t("analyticsTimeSavedHelp")}</p>
        <label htmlFor={`${id}-input`}>{t("analyticsBaseline")}</label>
        <div className="rp-dialog-input">
          <input
            ref={input}
            id={`${id}-input`}
            type="number"
            inputMode="decimal"
            min="0"
            max={MAX_BASELINE}
            step="0.5"
            value={draft}
            aria-invalid={!valid}
            aria-describedby={`${id}-hint`}
            onChange={(event) => setDraft(event.target.value)}
          />
          <span>{t("minutesUnit")}</span>
        </div>
        <p id={`${id}-hint`} className={valid ? "rp-hint" : "rp-hint rp-hint--error"}>
          {valid
            ? t("baselineHint", { max: MAX_BASELINE, fallback: DEFAULT_BASELINE })
            : t("baselineInvalid", { max: MAX_BASELINE })}
        </p>
        <p className="rp-hint">
          {t("baselineStorage")}
          {!settings.is_default && settings.updated_at && (
            <>
              {" "}
              {t("baselineUpdated", {
                name: settings.updated_by_name ?? t("adminRole"),
                when: new Intl.DateTimeFormat(i18n.language, {
                  dateStyle: "medium",
                  timeStyle: "short",
                }).format(new Date(settings.updated_at)),
              })}
            </>
          )}
        </p>
        {failed && (
          <p className="rp-hint rp-hint--error" role="alert">
            {t("baselineSaveError")}
          </p>
        )}
        <div className="rp-dialog-actions">
          <button type="button" onClick={onCancel}>
            {t("cancelEdit")}
          </button>
          <button type="submit" className="is-primary" disabled={!valid || saving}>
            {saving ? t("savingAdmin") : t("saveBaseline")}
          </button>
        </div>
      </form>
    </div>
  );
}
