import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { staffAuth } from "../lib/auth";

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
};
type TrendItem = { service_date: string; patient_count: number };
type Trend = { days: number; items: TrendItem[] };

const BASELINE_KEY = "opd-analytics-baseline-minutes";
const EMPTY_SUMMARY: Summary = {
  patients: 0,
  average_wait_seconds: null,
  average_consult_seconds: null,
  urgent_count: 0,
  language_mix: {},
  average_interview_seconds: null,
  confirmed_without_edits_percent: null,
  doctor_edit_rate_percent: null,
  department_load: [],
};

function loadBaseline(): number {
  try {
    const stored = window.localStorage.getItem(BASELINE_KEY);
    if (stored === null) return 3;
    const value = Number(stored);
    return Number.isFinite(value) && value >= 0 ? value : 3;
  } catch {
    return 3;
  }
}

function displayNumber(value: number | null | undefined, digits = 0): string {
  return value == null ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function AnalyticsDashboard() {
  const { t, i18n } = useTranslation("staff");
  const [baseline, setBaseline] = useState(loadBaseline);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(false);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["analytics-dashboard"],
    queryFn: async () => {
      const [summaryResponse, trendResponse] = await Promise.all([
        staffAuth.request("/analytics/summary"),
        staffAuth.request("/analytics/timeseries?days=30"),
      ]);
      if (!summaryResponse.ok || !trendResponse.ok) throw new Error("Analytics unavailable");
      const [summary, trend] = (await Promise.all([
        summaryResponse.json(),
        trendResponse.json(),
      ])) as [Summary, Trend];
      return { summary, trend: trend.items };
    },
  });
  const summary = data?.summary ?? EMPTY_SUMMARY;
  const dailyPatients = useMemo(() => {
    const byDate = new Map<string, number>();
    for (const item of data?.trend ?? []) byDate.set(item.service_date, (byDate.get(item.service_date) ?? 0) + item.patient_count);
    return [...byDate.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [data]);
  const maxDailyPatients = Math.max(1, ...dailyPatients.map(([, count]) => count));
  const totalDepartmentPatients = summary.department_load.reduce((sum, row) => sum + row.patients, 0);
  const languages = Object.entries(summary.language_mix).sort(([, a], [, b]) => b - a);

  function updateBaseline(value: number) {
    setBaseline(value);
    try { window.localStorage.setItem(BASELINE_KEY, String(value)); } catch { /* Keep current-session value. */ }
  }

  async function exportCsv() {
    setExporting(true);
    setExportError(false);
    try {
      const response = await staffAuth.request("/analytics/export.csv?days=30");
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
    <section className="analytics-page" aria-labelledby="analytics-title">
      <header className="analytics-header">
        <div>
          <h2 id="analytics-title">{t("reports")}</h2>
          <p>{t("analyticsDescription")}</p>
        </div>
        <button type="button" onClick={() => void exportCsv()} disabled={exporting || isLoading}>
          {exporting ? t("analyticsExporting") : t("analyticsExport")}
        </button>
      </header>

      {(isError || exportError) && <p className="analytics-error" role="alert">{t("analyticsError")}</p>}
      {isLoading ? <p role="status">{t("analyticsLoading")}</p> : <>
        <div className="analytics-kpis">
          <article><span>{t("analyticsPatients")}</span><strong>{displayNumber(summary.patients)}</strong></article>
          <article><span>{t("analyticsWait")}</span><strong>{displayNumber(summary.average_wait_seconds, 1)}<small> {t("secondsUnit")}</small></strong></article>
          <article><span>{t("analyticsConsult")}</span><strong>{displayNumber(summary.average_consult_seconds, 1)}<small> {t("secondsUnit")}</small></strong></article>
          <article><span>{t("analyticsUrgent")}</span><strong>{displayNumber(summary.urgent_count)}</strong></article>
          <article><span>{t("analyticsConfirmed")}</span><strong>{displayNumber(summary.confirmed_without_edits_percent, 1)}<small>%</small></strong></article>
          <article><span>{t("analyticsDoctorEditRate")}</span><strong>{displayNumber(summary.doctor_edit_rate_percent, 1)}<small>%</small></strong></article>
          <article><span>{t("analyticsInterview")}</span><strong>{displayNumber(summary.average_interview_seconds, 1)}<small> {t("secondsUnit")}</small></strong></article>
          <article className="analytics-baseline-card">
            <span>{t("analyticsTimeSaved")}</span>
            <strong>{displayNumber(summary.patients * baseline, 1)}<small> {t("minutesUnit")}</small></strong>
            <label htmlFor="analytics-baseline">{t("analyticsBaseline")}</label>
            <input id="analytics-baseline" type="number" min="0" max="60" step="0.5" value={baseline} onChange={(event) => updateBaseline(Number(event.target.value))} />
          </article>
        </div>

        <div className="analytics-panels">
          <article className="analytics-panel">
            <h3>{t("analyticsTrend")}</h3>
            {dailyPatients.length ? <div className="analytics-trend" role="img" aria-label={t("analyticsTrendAccessible")}>
              {dailyPatients.map(([day, count]) => <div className="analytics-trend-column" key={day} title={`${day}: ${count}`}>
                <span>{count}</span><i style={{ height: `${Math.max(3, count / maxDailyPatients * 100)}%` }} />
              </div>)}
            </div> : <p>{t("analyticsNoTrend")}</p>}
            <p className="analytics-caption">{t("analyticsLast30Days")}</p>
          </article>
          <article className="analytics-panel">
            <h3>{t("analyticsLanguages")}</h3>
            {languages.length ? <ul className="analytics-language-list">{languages.map(([code, count]) => <li key={code}>
              <span>{t(`language_${code}`, { defaultValue: code.toUpperCase() })}</span>
              <strong>{count.toLocaleString()}</strong>
              <i><span style={{ width: `${summary.patients ? count / summary.patients * 100 : 0}%` }} /></i>
            </li>)}</ul> : <p>{t("analyticsNoData")}</p>}
          </article>
        </div>

        <article className="analytics-panel analytics-departments">
          <h3>{t("analyticsDepartmentLoad")}</h3>
          {summary.department_load.length ? <div className="analytics-table-wrap"><table>
            <thead><tr><th>{t("department")}</th><th>{t("analyticsPatients")}</th><th>{t("analyticsUrgent")}</th><th>{t("analyticsWait")}</th><th>{t("analyticsShare")}</th></tr></thead>
            <tbody>{summary.department_load.map((row) => <tr key={row.department_id}>
              <th scope="row">{row.name[i18n.resolvedLanguage?.split("-")[0] ?? "en"] ?? row.name.en}</th>
              <td>{row.patients.toLocaleString()}</td><td>{row.urgent_count.toLocaleString()}</td>
              <td>{displayNumber(row.average_wait_seconds, 1)} {t("secondsUnit")}</td>
              <td>{displayNumber(totalDepartmentPatients ? row.patients / totalDepartmentPatients * 100 : 0, 1)}%</td>
            </tr>)}</tbody>
          </table></div> : <p>{t("analyticsNoData")}</p>}
        </article>
      </>}
    </section>
  );
}
