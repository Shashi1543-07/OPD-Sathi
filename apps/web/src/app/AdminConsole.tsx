import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Building2,
  Eye,
  EyeOff,
  KeyRound,
  Monitor,
  Network,
  PlusCircle,
  Search,
  UserRound,
  Users,
} from "lucide-react";
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
  created_at?: string | null;
};
type Page<T> = { items: T[]; next_cursor: string | null };
type Activation = { kiosk: string; code: string } | null;
type Filters = { search: string; role: string; status: string };
type Pending = { resource: Resource; item: { id: string; is_active: boolean }; name: string };

const resources: Resource[] = ["users", "kiosks", "departments"];
const TAB_ICON: Record<Resource, typeof Users> = {
  users: Users,
  kiosks: Monitor,
  departments: Network,
};
const NO_FILTERS: Filters = { search: "", role: "", status: "" };
/** A kiosk that has not reported for this long is treated as offline (it pings each minute). */
const ONLINE_WINDOW_MS = 3 * 60_000;
type Connection = "online" | "offline" | "unseen" | "off";
function connectionOf(kiosk: Kiosk, now: number): Connection {
  if (!kiosk.is_active) return "off";
  if (!kiosk.last_seen_at) return "unseen";
  return now - Date.parse(kiosk.last_seen_at) <= ONLINE_WINDOW_MS ? "online" : "offline";
}

/** The API pages 100 records at a time; hospital lists are small, so read them whole (up to 2,000). */
const MAX_PAGES = 20;
const cap = (value: string) => `${value[0].toUpperCase()}${value.slice(1)}`;

function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const last = words.at(-1) ?? "";
  // "Demo Doctor 01" -> "D1", "Demo Doctor 10" -> "D10": the number tells similar names apart.
  if (words.length > 1 && /^\d+$/.test(last)) {
    return `${words[words.length - 2][0]}${String(Number(last)).slice(-2)}`.toUpperCase();
  }
  return words
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
}

export function AdminConsole() {
  const { t, i18n } = useTranslation("staff");
  const [resource, setResource] = useState<Resource>("users");
  const [departments, setDepartments] = useState<Department[]>([]);
  const [users, setUsers] = useState<StaffRecord[]>([]);
  const [kiosks, setKiosks] = useState<Kiosk[]>([]);
  const [loaded, setLoaded] = useState<Partial<Record<Resource, boolean>>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [activation, setActivation] = useState<Activation>(null);
  const [filters, setFilters] = useState<Record<Resource, Filters>>({
    users: NO_FILTERS,
    kiosks: NO_FILTERS,
    departments: NO_FILTERS,
  });
  const [pending, setPending] = useState<Pending | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const baseId = useId();
  const tabRefs = useRef<Partial<Record<Resource, HTMLButtonElement | null>>>({});

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

  const fetchAll = useCallback(
    async <T,>(selected: Resource): Promise<T[]> => {
      const items: T[] = [];
      let cursor: string | null = null;
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const query = new URLSearchParams({ limit: "100" });
        if (cursor) query.set("cursor", cursor);
        const body = (await request(`/admin/${selected}?${query}`)) as Page<T>;
        items.push(...body.items);
        cursor = body.next_cursor;
        if (!cursor) break;
      }
      return items;
    },
    [request],
  );

  const load = useCallback(
    async (selected: Resource) => {
      setLoading(true);
      setError("");
      try {
        if (selected !== "departments") {
          setDepartments(await fetchAll<Department>("departments"));
        }
        if (selected === "departments") setDepartments(await fetchAll<Department>("departments"));
        else if (selected === "users") setUsers(await fetchAll<StaffRecord>("users"));
        else setKiosks(await fetchAll<Kiosk>("kiosks"));
        setLoaded((current) => ({ ...current, [selected]: true }));
      } catch (cause) {
        setError(
          cause instanceof Error ? cause.message : t("adminRequestError"),
        );
      } finally {
        setLoading(false);
      }
    },
    [fetchAll, t],
  );

  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => (active ? load(resource) : undefined));
    return () => {
      active = false;
    };
  }, [load, resource]);

  // Kiosks report in about once a minute; keep their connection state current while watched.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (resource !== "kiosks") return;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      void load("kiosks");
    }, 60_000);
    return () => window.clearInterval(timer);
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
      setShowPassword(false);
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

  async function toggle(target: Resource, item: { id: string; is_active: boolean }) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await request(`/admin/${target}/${item.id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ is_active: !item.is_active }),
      });
      await load(target);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  /** Deactivating asks first; activating again is harmless and happens at once. */
  function requestToggle(item: { id: string; is_active: boolean }, name: string) {
    if (item.is_active) setPending({ resource, item, name });
    else void toggle(resource, item);
  }

  const departmentById = useMemo(
    () => new Map(departments.map((department) => [department.id, department])),
    [departments],
  );
  const filter = filters[resource];
  const setFilter = (patch: Partial<Filters>) =>
    setFilters((current) => ({ ...current, [resource]: { ...current[resource], ...patch } }));
  const needle = filter.search.trim().toLowerCase();
  const statusOk = (item: { is_active: boolean }) =>
    !filter.status || (filter.status === "active") === item.is_active;
  const visibleUsers = users.filter(
    (item) =>
      statusOk(item) &&
      (!filter.role || item.role === filter.role) &&
      (!needle || `${item.name} ${item.email}`.toLowerCase().includes(needle)),
  );
  const visibleKiosks = kiosks.filter(
    (item) => statusOk(item) && (!needle || `${item.label} ${item.id}`.toLowerCase().includes(needle)),
  );
  const visibleDepartments = departments.filter(
    (item) =>
      statusOk(item) &&
      (!needle ||
        `${item.code} ${item.token_prefix} ${item.name_en} ${item.name_hi} ${item.name_mr}`
          .toLowerCase()
          .includes(needle)),
  );
  const total = { users: users.length, kiosks: kiosks.length, departments: departments.length }[resource];
  const shown = {
    users: visibleUsers.length,
    kiosks: visibleKiosks.length,
    departments: visibleDepartments.length,
  }[resource];
  const filtersActive = Boolean(needle || filter.role || filter.status);
  const dateTime = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });
  const relative = new Intl.RelativeTimeFormat(i18n.language, { numeric: "auto" });
  const ago = (iso: string) => {
    const minutes = Math.round((Date.parse(iso) - now) / 60_000);
    if (minutes > -60) return relative.format(minutes, "minute");
    const hours = Math.round(minutes / 60);
    return hours > -24 ? relative.format(hours, "hour") : relative.format(Math.round(hours / 24), "day");
  };
  const activeKiosks = kiosks.filter((kiosk) => kiosk.is_active);
  const onlineKiosks = activeKiosks.filter((kiosk) => connectionOf(kiosk, now) === "online");
  const dateOnly = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" });
  const roleLabel = (role: string) => t(`${role}Role`, { defaultValue: cap(role) });
  const departmentLabel = (department: Department | undefined) => department?.name_en ?? t("noDepartment");

  if (staffAuth.user?.role !== "admin") {
    return (
      <section className="staff-admin">
        <h2>{t("settings")}</h2>
        <p role="alert">{t("adminOnly")}</p>
      </section>
    );
  }

  function onTabKey(event: React.KeyboardEvent<HTMLButtonElement>) {
    const index = resources.indexOf(resource);
    const next =
      event.key === "ArrowRight"
        ? resources[(index + 1) % resources.length]
        : event.key === "ArrowLeft"
          ? resources[(index - 1 + resources.length) % resources.length]
          : event.key === "Home"
            ? resources[0]
            : event.key === "End"
              ? resources[resources.length - 1]
              : undefined;
    if (!next) return;
    event.preventDefault();
    setResource(next);
    tabRefs.current[next]?.focus();
  }

  const statusPill = (active: boolean) => (
    <span className={`adm-status${active ? " is-active" : ""}`}>
      {active ? t("active") : t("inactive")}
    </span>
  );
  const toggleButton = (item: { id: string; is_active: boolean }, name: string, self = false) => (
    <button
      type="button"
      className={`adm-toggle${item.is_active ? "" : " is-activate"}`}
      disabled={saving || self}
      title={self ? t("cannotDeactivateSelf") : undefined}
      aria-label={`${item.is_active ? t("deactivate") : t("activate")} ${name}`}
      onClick={() => requestToggle(item, name)}
    >
      {item.is_active ? t("deactivate") : t("activate")}
    </button>
  );

  let rows: ReactNode;
  let head: ReactNode;
  if (resource === "users") {
    head = (
      <tr>
        <th scope="col">{t("staffName")}</th>
        <th scope="col">{t("staffRole")}</th>
        <th scope="col">{t("department")}</th>
        <th scope="col">{t("statusColumn")}</th>
        <th scope="col" className="adm-col-action">{t("actionsColumn")}</th>
      </tr>
    );
    rows = visibleUsers.map((item) => {
      const self = item.id === staffAuth.user?.id;
      return (
        <tr key={item.id} className={item.is_active ? undefined : "is-inactive"}>
          <td data-label={t("staffName")}>
            <span className="adm-person">
              <span className={`adm-avatar adm-avatar--${item.role}`} aria-hidden="true">
                {initials(item.name)}
              </span>
              <span className="adm-person-text">
                <strong>
                  {item.name}
                  {self && <span className="adm-you">{t("youLabel")}</span>}
                </strong>
                <span>{item.email}</span>
              </span>
            </span>
          </td>
          <td data-label={t("staffRole")}>{roleLabel(item.role)}</td>
          <td data-label={t("department")}>
            {item.department_id ? departmentLabel(departmentById.get(item.department_id)) : t("noDepartment")}
          </td>
          <td data-label={t("statusColumn")}>{statusPill(item.is_active)}</td>
          <td className="adm-col-action">{toggleButton(item, item.name, self)}</td>
        </tr>
      );
    });
  } else if (resource === "kiosks") {
    head = (
      <tr>
        <th scope="col">{t("kioskLabel")}</th>
        <th scope="col">{t("kioskAdded")}</th>
        <th scope="col">{t("connectionColumn")}</th>
        <th scope="col">{t("statusColumn")}</th>
        <th scope="col" className="adm-col-action">{t("actionsColumn")}</th>
      </tr>
    );
    rows = visibleKiosks.map((item) => (
      <tr key={item.id} className={item.is_active ? undefined : "is-inactive"}>
        <td data-label={t("kioskLabel")}>
          <span className="adm-person">
            <span className="adm-avatar adm-avatar--kiosk" aria-hidden="true">
              <Monitor size={16} />
            </span>
            <span className="adm-person-text">
              <strong>{item.label}</strong>
              <span title={item.id}>{t("kioskId", { id: item.id.slice(-8) })}</span>
            </span>
          </span>
        </td>
        <td data-label={t("kioskAdded")}>
          {item.created_at ? dateOnly.format(new Date(item.created_at)) : "—"}
        </td>
        <td data-label={t("connectionColumn")}>
          {(() => {
            const state = connectionOf(item, now);
            return (
              <span className="adm-connection">
                <span className={`adm-conn adm-conn--${state}`}>{t(`kioskConn_${state}`)}</span>
                {item.last_seen_at && state !== "online" && (
                  <span className="adm-muted" title={dateTime.format(new Date(item.last_seen_at))}>
                    {t("kioskLastSeenAt", { when: ago(item.last_seen_at) })}
                  </span>
                )}
              </span>
            );
          })()}
        </td>
        <td data-label={t("statusColumn")}>{statusPill(item.is_active)}</td>
        <td className="adm-col-action">{toggleButton(item, item.label)}</td>
      </tr>
    ));
  } else {
    head = (
      <tr>
        <th scope="col">{t("department")}</th>
        <th scope="col">{t("prefixColumn")}</th>
        <th scope="col">{t("localNames")}</th>
        <th scope="col">{t("statusColumn")}</th>
        <th scope="col" className="adm-col-action">{t("actionsColumn")}</th>
      </tr>
    );
    rows = visibleDepartments.map((item) => (
      <tr key={item.id} className={item.is_active ? undefined : "is-inactive"}>
        <td data-label={t("department")}>
          <span className="adm-person">
            <span className="adm-avatar adm-avatar--department" aria-hidden="true">
              <Building2 size={16} />
            </span>
            <span className="adm-person-text">
              <strong>{item.name_en}</strong>
              <span>
                <code className="adm-code">{item.code}</code>
              </span>
            </span>
          </span>
        </td>
        <td data-label={t("prefixColumn")}>
          <code className="adm-code">{item.token_prefix}</code>
        </td>
        <td data-label={t("localNames")}>
          <span className="adm-local">
            <span lang="hi">{item.name_hi}</span>
            <span lang="mr">{item.name_mr}</span>
          </span>
        </td>
        <td data-label={t("statusColumn")}>{statusPill(item.is_active)}</td>
        <td className="adm-col-action">{toggleButton(item, item.name_en)}</td>
      </tr>
    ));
  }

  const listTitle = { users: t("staffAccounts"), kiosks: t("kioskList"), departments: t("departmentList") }[resource];
  const listSubtitle = {
    users: t("staffAccountsHint"),
    kiosks: loaded.kiosks
      ? t("kioskOnlineSummary", { online: onlineKiosks.length, count: activeKiosks.length })
      : t("kioskListHint"),
    departments: t("departmentListHint"),
  }[resource];
  const formHint = {
    users: t("createUserHint"),
    kiosks: t("createKioskHint"),
    departments: t("createDepartmentHint"),
  }[resource];
  const TabIcon = TAB_ICON[resource];

  return (
    <section className="adm" aria-labelledby="admin-title">
      <header className="adm-head">
        <h2 id="admin-title">{t("adminTitle")}</h2>
        <p>{t("adminDescription")}</p>
      </header>
      <div className="adm-tabs" role="tablist" aria-label={t("adminResources")}>
        {resources.map((item) => {
          const Icon = TAB_ICON[item];
          return (
            <button
              key={item}
              ref={(node) => {
                tabRefs.current[item] = node;
              }}
              id={`${baseId}-tab-${item}`}
              type="button"
              role="tab"
              aria-selected={resource === item}
              aria-controls={`${baseId}-panel`}
              tabIndex={resource === item ? 0 : -1}
              onClick={() => setResource(item)}
              onKeyDown={onTabKey}
            >
              <Icon aria-hidden="true" size={17} />
              {t(`admin${cap(item)}`)}
            </button>
          );
        })}
      </div>
      {error && (
        <p className="adm-banner adm-banner--error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="adm-banner adm-banner--ok" role="status">
          {notice}
        </p>
      )}
      <div
        className="adm-body"
        id={`${baseId}-panel`}
        role="tabpanel"
        aria-labelledby={`${baseId}-tab-${resource}`}
      >
        <div className="adm-side">
          {activation && (
            <aside className="adm-activation" aria-live="polite">
              <h3>
                <KeyRound aria-hidden="true" size={17} /> {t("activationCodeTitle")}
              </h3>
              <p>{t("activationCodeOnce", { kiosk: activation.kiosk })}</p>
              <code>{activation.code}</code>
              <button type="button" onClick={() => setActivation(null)}>
                {t("dismissActivation")}
              </button>
            </aside>
          )}
          <form className="adm-card adm-form" onSubmit={(event) => void create(event)}>
            <div className="adm-card-head">
              <span className="adm-card-icon" aria-hidden="true">
                {resource === "users" ? <UserRound size={20} /> : <TabIcon size={20} />}
              </span>
              <div>
                <h3>{t(`adminCreate${cap(resource)}`)}</h3>
                <p>{formHint}</p>
              </div>
            </div>
            {resource === "departments" && (
              <>
                <div className="adm-row2">
                  <label>
                    <span>
                      {t("departmentCode")} <em aria-hidden="true">*</em>
                    </span>
                    <input name="code" pattern="[A-Z0-9_]+" required maxLength={32} placeholder="GENMED" />
                    <small>{t("departmentCodeHint")}</small>
                  </label>
                  <label>
                    <span>
                      {t("tokenPrefix")} <em aria-hidden="true">*</em>
                    </span>
                    <input name="token_prefix" pattern="[A-Z0-9]+" required maxLength={8} placeholder="MED" />
                    <small>{t("tokenPrefixHint")}</small>
                  </label>
                </div>
                <label>
                  <span>
                    {t("nameEnglish")} <em aria-hidden="true">*</em>
                  </span>
                  <input name="name_en" required maxLength={200} />
                </label>
                <label>
                  <span>
                    {t("nameHindi")} <em aria-hidden="true">*</em>
                  </span>
                  <input name="name_hi" required maxLength={200} lang="hi" />
                </label>
                <label>
                  <span>
                    {t("nameMarathi")} <em aria-hidden="true">*</em>
                  </span>
                  <input name="name_mr" required maxLength={200} lang="mr" />
                </label>
              </>
            )}
            {resource === "users" && (
              <>
                <label>
                  <span>
                    {t("staffName")} <em aria-hidden="true">*</em>
                  </span>
                  <input name="name" required maxLength={200} autoComplete="off" />
                </label>
                <label>
                  <span>
                    {t("email")} <em aria-hidden="true">*</em>
                  </span>
                  <input name="email" type="email" required maxLength={320} autoComplete="off" placeholder="staff@example.com" />
                </label>
                <label>
                  <span>
                    {t("temporaryPassword")} <em aria-hidden="true">*</em>
                  </span>
                  <span className="adm-password">
                    <input
                      name="password"
                      type={showPassword ? "text" : "password"}
                      required
                      minLength={12}
                      maxLength={1024}
                      autoComplete="new-password"
                    />
                    <button
                      type="button"
                      aria-label={showPassword ? t("hidePassword") : t("showPassword")}
                      aria-pressed={showPassword}
                      onClick={() => setShowPassword((value) => !value)}
                    >
                      {showPassword ? <EyeOff aria-hidden="true" size={16} /> : <Eye aria-hidden="true" size={16} />}
                    </button>
                  </span>
                  <small>{t("passwordHint")}</small>
                </label>
                <div className="adm-row2">
                  <label>
                    <span>
                      {t("staffRole")} <em aria-hidden="true">*</em>
                    </span>
                    <select name="role" required>
                      <option value="doctor">{t("doctorRole")}</option>
                      <option value="nurse">{t("nurseRole")}</option>
                      <option value="admin">{t("adminRole")}</option>
                    </select>
                  </label>
                  <label>
                    <span>{t("department")}</span>
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
                </div>
              </>
            )}
            {resource === "kiosks" && (
              <label>
                <span>
                  {t("kioskLabel")} <em aria-hidden="true">*</em>
                </span>
                <input name="label" required maxLength={100} placeholder={t("kioskLabelPlaceholder")} />
                <small>{t("kioskLabelHint")}</small>
              </label>
            )}
            <p className="adm-required">
              <em aria-hidden="true">*</em> {t("requiredFields")}
            </p>
            <button type="submit" className="adm-submit" disabled={saving}>
              <PlusCircle aria-hidden="true" size={17} />
              {saving ? t("savingAdmin") : t(`adminCreate${cap(resource)}`)}
            </button>
          </form>
        </div>

        <section className="adm-card adm-list" aria-labelledby={`${baseId}-list`}>
          <div className="adm-card-head">
            <span className="adm-card-icon" aria-hidden="true">
              <TabIcon size={20} />
            </span>
            <div>
              <h3 id={`${baseId}-list`}>{listTitle}</h3>
              <p>{listSubtitle}</p>
            </div>
            <p className="adm-total">
              {t("adminTotal")} <strong>{loaded[resource] ? total : "—"}</strong>
            </p>
          </div>
          <div className="adm-toolbar" role="search">
            <label className="adm-search">
              <Search aria-hidden="true" size={16} />
              <input
                type="search"
                value={filter.search}
                aria-label={t(`adminSearch${cap(resource)}`)}
                placeholder={t(`adminSearch${cap(resource)}`)}
                onChange={(event) => setFilter({ search: event.target.value })}
              />
            </label>
            {resource === "users" && (
              <select
                aria-label={t("filterRole")}
                value={filter.role}
                onChange={(event) => setFilter({ role: event.target.value })}
              >
                <option value="">{t("allRoles")}</option>
                <option value="doctor">{t("doctorRole")}</option>
                <option value="nurse">{t("nurseRole")}</option>
                <option value="admin">{t("adminRole")}</option>
              </select>
            )}
            <select
              aria-label={t("filterStatus")}
              value={filter.status}
              onChange={(event) => setFilter({ status: event.target.value })}
            >
              <option value="">{t("allStatuses")}</option>
              <option value="active">{t("active")}</option>
              <option value="inactive">{t("inactive")}</option>
            </select>
          </div>
          <p className="adm-count" aria-live="polite">
            {loaded[resource] && !loading
              ? filtersActive
                ? t("adminShowing", { shown, total })
                : t("adminShowingAll", { count: total })
              : ""}
            {filtersActive && (
              <button type="button" className="adm-clear" onClick={() => setFilter(NO_FILTERS)}>
                {t("clearFilters")}
              </button>
            )}
          </p>
          <div
            className="adm-scroll"
            role="region"
            aria-label={t("adminResults", { title: listTitle })}
            tabIndex={0}
          >
            {loading && !loaded[resource] ? (
              <p className="adm-empty" role="status">
                {t("loadingAdminRecords")}
              </p>
            ) : shown === 0 ? (
              <p className="adm-empty">{filtersActive ? t("adminNoMatches") : t("adminEmpty")}</p>
            ) : (
              <table className={`adm-table adm-table--${resource}`}>
                <thead>{head}</thead>
                <tbody>{rows}</tbody>
              </table>
            )}
          </div>
        </section>
      </div>

      {pending && (
        <div
          className="rp-dialog-backdrop"
          onKeyDown={(event) => {
            if (event.key === "Escape") setPending(null);
          }}
        >
          <section
            className="rp-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={`${baseId}-confirm`}
            aria-describedby={`${baseId}-confirm-text`}
          >
            <h3 id={`${baseId}-confirm`}>{t("confirmDeactivateTitle", { name: pending.name })}</h3>
            <p id={`${baseId}-confirm-text`}>
              {pending.resource === "users" ? t("confirmDeactivateUser") : t("confirmDeactivateOther")}
            </p>
            <div className="rp-dialog-actions">
              <button type="button" autoFocus onClick={() => setPending(null)}>
                {t("cancelEdit")}
              </button>
              <button
                type="button"
                className="is-danger"
                onClick={() => {
                  const target = pending;
                  setPending(null);
                  void toggle(target.resource, target.item);
                }}
              >
                {t("deactivate")}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
