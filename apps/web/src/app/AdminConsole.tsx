import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { StaffRole } from "../lib/auth";
import { staffAuth } from "../lib/auth";

type Resource = "departments" | "users" | "kiosks";
type Department = {
  id: string;
  code: string;
  token_prefix: string;
  name_en: string;
  name_hi: string;
  name_mr: string;
  is_active: boolean;
};
type StaffRecord = {
  id: string;
  role: StaffRole;
  name: string;
  email: string;
  department_id: string | null;
  is_active: boolean;
};
type Kiosk = {
  id: string;
  label: string;
  is_active: boolean;
  last_seen_at: string | null;
};
type Page<T> = { items: T[]; next_cursor: string | null };
type Activation = { kiosk: string; code: string } | null;

const resources: Resource[] = ["users", "kiosks", "departments"];

export function AdminConsole() {
  const { t } = useTranslation("staff");
  const [resource, setResource] = useState<Resource>("users");
  const [departments, setDepartments] = useState<Department[]>([]);
  const [users, setUsers] = useState<StaffRecord[]>([]);
  const [kiosks, setKiosks] = useState<Kiosk[]>([]);
  const [cursors, setCursors] = useState<Partial<Record<Resource, string>>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [activation, setActivation] = useState<Activation>(null);

  const request = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const response = await staffAuth.request(path, init);
      if (!response.ok) {
        const problem = (await response.json().catch(() => ({}))) as {
          detail?: string;
          title?: string;
        };
        throw new Error(
          problem.detail ?? problem.title ?? t("adminRequestError"),
        );
      }
      return response.status === 204 ? null : response.json();
    },
    [t],
  );

  const load = useCallback(
    async (selected: Resource, cursor?: string) => {
      if (!cursor) setLoading(true);
      setError("");
      try {
        if (!cursor && selected !== "departments") {
          const departmentPage = (await request(
            "/admin/departments?limit=100",
          )) as Page<Department>;
          setDepartments(departmentPage.items);
        }
        const query = new URLSearchParams({ limit: "100" });
        if (cursor) query.set("cursor", cursor);
        const page = (await request(`/admin/${selected}?${query}`)) as Page<
          Department | StaffRecord | Kiosk
        >;
        if (selected === "departments") {
          setDepartments((current) =>
            cursor
              ? [...current, ...(page.items as Department[])]
              : (page.items as Department[]),
          );
        } else if (selected === "users") {
          setUsers((current) =>
            cursor
              ? [...current, ...(page.items as StaffRecord[])]
              : (page.items as StaffRecord[]),
          );
        } else {
          setKiosks((current) =>
            cursor
              ? [...current, ...(page.items as Kiosk[])]
              : (page.items as Kiosk[]),
          );
        }
        setCursors((current) => ({
          ...current,
          [selected]: page.next_cursor ?? undefined,
        }));
      } catch (cause) {
        setError(
          cause instanceof Error ? cause.message : t("adminRequestError"),
        );
      } finally {
        setLoading(false);
      }
    },
    [request, t],
  );

  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => (active ? load(resource) : undefined));
    return () => {
      active = false;
    };
  }, [load, resource]);

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const formElement = event.currentTarget;
    const payload: Record<string, FormDataEntryValue | null> =
      Object.fromEntries(form.entries());
    if (resource === "users" && !payload.department_id)
      payload.department_id = null;
    setSaving(true);
    setError("");
    setNotice("");
    setActivation(null);
    try {
      const created = (await request(`/admin/${resource}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify(payload),
      })) as { id: string; activation_code?: string; label?: string };
      formElement.reset();
      if (created.activation_code) {
        setActivation({
          kiosk: created.label ?? t("newKiosk"),
          code: created.activation_code,
        });
        setResource("kiosks");
        await load("kiosks");
      } else {
        setNotice(t("adminCreated"));
        await load(resource);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  async function toggle(item: { id: string; is_active: boolean }) {
    setSaving(true);
    setError("");
    try {
      await request(`/admin/${resource}/${item.id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ is_active: !item.is_active }),
      });
      await load(resource);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  if (staffAuth.user?.role !== "admin") {
    return (
      <section className="staff-admin">
        <h2>{t("settings")}</h2>
        <p role="alert">{t("adminOnly")}</p>
      </section>
    );
  }

  return (
    <section className="staff-admin" aria-labelledby="admin-title">
      <header className="staff-queue-heading">
        <div>
          <h2 id="admin-title">{t("adminTitle")}</h2>
          <p>{t("adminDescription")}</p>
        </div>
      </header>
      <div
        className="staff-admin-tabs"
        role="tablist"
        aria-label={t("adminResources")}
      >
        {resources.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={resource === item}
            onClick={() => setResource(item)}
          >
            {t(`admin${item[0].toUpperCase()}${item.slice(1)}`)}
          </button>
        ))}
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {activation && (
        <aside className="staff-activation" aria-live="polite">
          <h3>{t("activationCodeTitle")}</h3>
          <p>{t("activationCodeOnce", { kiosk: activation.kiosk })}</p>
          <code>{activation.code}</code>
          <button type="button" onClick={() => setActivation(null)}>
            {t("dismissActivation")}
          </button>
        </aside>
      )}
      <div className="staff-admin-layout">
        <form
          className="staff-admin-form"
          onSubmit={(event) => void create(event)}
        >
          <h3>
            {t(`adminCreate${resource[0].toUpperCase()}${resource.slice(1)}`)}
          </h3>
          {resource === "departments" && (
            <>
              <label>
                {t("departmentCode")}
                <input
                  name="code"
                  pattern="[A-Z0-9_]+"
                  required
                  maxLength={32}
                />
              </label>
              <label>
                {t("tokenPrefix")}
                <input
                  name="token_prefix"
                  pattern="[A-Z0-9]+"
                  required
                  maxLength={8}
                />
              </label>
              <label>
                {t("nameEnglish")}
                <input name="name_en" required maxLength={200} />
              </label>
              <label>
                {t("nameHindi")}
                <input name="name_hi" required maxLength={200} />
              </label>
              <label>
                {t("nameMarathi")}
                <input name="name_mr" required maxLength={200} />
              </label>
            </>
          )}
          {resource === "users" && (
            <>
              <label>
                {t("staffName")}
                <input name="name" required maxLength={200} />
              </label>
              <label>
                {t("email")}
                <input name="email" type="email" required maxLength={320} />
              </label>
              <label>
                {t("temporaryPassword")}
                <input
                  name="password"
                  type="password"
                  required
                  minLength={12}
                  maxLength={1024}
                  autoComplete="new-password"
                />
              </label>
              <label>
                {t("staffRole")}
                <select name="role" required>
                  <option value="doctor">{t("doctorRole")}</option>
                  <option value="nurse">{t("nurseRole")}</option>
                  <option value="admin">{t("adminRole")}</option>
                </select>
              </label>
              <label>
                {t("department")}
                <select name="department_id">
                  <option value="">{t("noDepartment")}</option>
                  {departments
                    .filter((d) => d.is_active)
                    .map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name_en}
                      </option>
                    ))}
                </select>
              </label>
            </>
          )}
          {resource === "kiosks" && (
            <label>
              {t("kioskLabel")}
              <input name="label" required maxLength={100} />
            </label>
          )}
          <button type="submit" disabled={saving}>
            {saving ? t("savingAdmin") : t("createAdminRecord")}
          </button>
        </form>
        <div className="staff-admin-list" aria-live="polite">
          {loading ? (
            <p role="status">{t("loadingAdminRecords")}</p>
          ) : resource === "departments" ? (
            departments.map((item) => (
              <AdminRow
                key={item.id}
                title={`${item.name_en} · ${item.code}`}
                subtitle={`${item.name_hi} / ${item.name_mr}`}
                item={item}
                onToggle={() => void toggle(item)}
                disabled={saving}
                t={t}
              />
            ))
          ) : resource === "users" ? (
            users.map((item) => (
              <AdminRow
                key={item.id}
                title={item.name}
                subtitle={`${item.role} · ${item.email}`}
                item={item}
                onToggle={() => void toggle(item)}
                disabled={saving || item.id === staffAuth.user?.id}
                t={t}
              />
            ))
          ) : (
            kiosks.map((item) => (
              <AdminRow
                key={item.id}
                title={item.label}
                subtitle={
                  item.last_seen_at
                    ? t("kioskLastSeen", {
                        date: new Date(item.last_seen_at).toLocaleString(),
                      })
                    : t("kioskNeverSeen")
                }
                item={item}
                onToggle={() => void toggle(item)}
                disabled={saving}
                t={t}
              />
            ))
          )}
          {!loading &&
            (resource === "departments"
              ? departments.length
              : resource === "users"
                ? users.length
                : kiosks.length) === 0 && <p>{t("adminEmpty")}</p>}
          {cursors[resource] && (
            <button
              type="button"
              disabled={loading}
              onClick={() => void load(resource, cursors[resource])}
            >
              {t("loadMoreAdmin")}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function AdminRow({
  title,
  subtitle,
  item,
  onToggle,
  disabled,
  t,
}: {
  title: string;
  subtitle: string;
  item: { is_active: boolean };
  onToggle: () => void;
  disabled: boolean;
  t: (key: string) => string;
}) {
  return (
    <article className="staff-admin-row">
      <div>
        <strong>{title}</strong>
        <span>{subtitle}</span>
      </div>
      <span>{item.is_active ? t("active") : t("inactive")}</span>
      <button type="button" disabled={disabled} onClick={onToggle}>
        {item.is_active ? t("deactivate") : t("activate")}
      </button>
    </article>
  );
}
