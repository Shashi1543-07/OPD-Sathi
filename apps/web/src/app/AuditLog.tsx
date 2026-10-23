import { FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { Download, Search } from "lucide-react";
import { Button, Toast } from "../components/ui";
import { staffAuth } from "../lib/auth";

type AuditEvent = {
  id: string;
  actor_type: string;
  actor_id: string | null;
  actor_name: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
};
type Filters = { record: string; person: string; action: string; date_from: string; date_to: string };
const EMPTY: Filters = { record: "", person: "", action: "", date_from: "", date_to: "" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function query(filters: Filters, cursor?: string | null) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value.trim()) params.set(key, value.trim());
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

/**
 * Administrators search the hospital's audit log for an investigation and export what they
 * find (docs/runbooks/audit-investigation.md). The server records every search and export.
 */
export function AuditLog() {
  const { t, i18n } = useTranslation("staff");
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(
    async (selected: Filters, after: string | null = null) => {
      setLoading(true);
      setError("");
      try {
        const response = await staffAuth.request(`/audit?${query(selected, after)}`, {
          cache: "no-store",
        });
        if (!response.ok) {
          const problem = (await response.json().catch(() => ({}))) as { detail?: string };
          throw new Error(problem.detail ?? t("adminRequestError"));
        }
        const body = (await response.json()) as { items: AuditEvent[]; next_cursor: string | null };
        setItems((current) => (after ? [...current, ...body.items] : body.items));
        setCursor(body.next_cursor);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : t("adminRequestError"));
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    void Promise.resolve().then(() => load(EMPTY));
  }, [load]);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    for (const key of ["record", "person"] as const) {
      if (draft[key].trim() && !UUID.test(draft[key].trim())) {
        setError(t("auditIdInvalid"));
        return;
      }
    }
    setFilters(draft);
    void load(draft);
  }

  async function exportCsv() {
    setError("");
    const response = await staffAuth.request(`/audit/export?${query(filters)}`, { cache: "no-store" });
    if (!response.ok) {
      setError(t("adminRequestError"));
      return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? "audit.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  const when = (value: string) =>
    new Intl.DateTimeFormat(i18n.language === "en" ? "en-IN" : i18n.language, {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(value));
  const field = (key: keyof Filters, label: string, type = "text") => (
    <label>
      {label}
      <input
        type={type}
        value={draft[key]}
        onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))}
      />
    </label>
  );

  return (
    <section className="acct audit" aria-labelledby="audit-title">
      <h2 id="audit-title">{t("auditLog")}</h2>
      <p className="acct-who">{t("auditLogHint")}</p>
      <form className="audit-filters" onSubmit={search} aria-label={t("auditFilters")}>
        {field("record", t("auditRecord"))}
        {field("person", t("auditPerson"))}
        {field("action", t("auditAction"))}
        {field("date_from", t("auditFrom"), "date")}
        {field("date_to", t("auditTo"), "date")}
        <div className="acct-actions">
          <Button type="submit" icon={<Search aria-hidden="true" size={16} />} busy={loading}>
            {t("auditSearch")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            icon={<Download aria-hidden="true" size={16} />}
            onClick={() => void exportCsv()}
          >
            {t("auditExport")}
          </Button>
        </div>
      </form>
      <div aria-live="polite">{error && <Toast tone="error">{error}</Toast>}</div>
      <div className="adm-scroll" id="audit-results" role="region" aria-label={t("auditLog")} tabIndex={0}>
        {items.length === 0 && !loading ? (
          <p className="adm-empty">{t("auditEmpty")}</p>
        ) : (
          <table className="adm-table audit-table">
            <thead>
              <tr>
                <th scope="col">{t("auditTime")}</th>
                <th scope="col">{t("auditWho")}</th>
                <th scope="col">{t("auditAction")}</th>
                <th scope="col">{t("auditRecord")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td data-label={t("auditTime")}>{when(item.created_at)}</td>
                  <td data-label={t("auditWho")}>
                    {item.actor_name ?? item.actor_type}
                    {item.actor_id && (
                      <button
                        type="button"
                        className="audit-id"
                        title={t("auditFilterByPerson")}
                        onClick={() => setDraft((current) => ({ ...current, person: item.actor_id ?? "" }))}
                      >
                        {item.actor_id.slice(0, 8)}
                      </button>
                    )}
                  </td>
                  <td data-label={t("auditAction")}>
                    <code>{item.action}</code>
                  </td>
                  <td data-label={t("auditRecord")}>
                    {item.resource_type ?? ""}
                    {item.resource_id && (
                      <button
                        type="button"
                        className="audit-id"
                        title={t("auditFilterByRecord")}
                        onClick={() => setDraft((current) => ({ ...current, record: item.resource_id ?? "" }))}
                      >
                        {item.resource_id.slice(0, 8)}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {cursor && (
        <Button type="button" variant="secondary" busy={loading} onClick={() => void load(filters, cursor)}>
          {t("auditMore")}
        </Button>
      )}
    </section>
  );
}
