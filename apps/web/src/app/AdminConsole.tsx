import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { QRCodeSVG } from "qrcode.react";
import {
  Building2,
  Check,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Link2,
  LockOpen,
  LogOut,
  Pencil,
  Monitor,
  MoreHorizontal,
  Network,
  PlusCircle,
  Printer,
  QrCode,
  RefreshCw,
  Search,
  ShieldOff,
  Smartphone,
  Trash2,
  UserRound,
  Users,
} from "lucide-react";
import { Popover } from "../components/ui/Popover";
import type { StaffRole } from "../lib/auth";
import { staffAuth } from "../lib/auth";
import { newRequestId } from "../lib/ids";
import { InterviewSettingsCard } from "./InterviewSettingsCard";
import { QueueSettingsCard } from "./QueueSettingsCard";

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
  locked_until?: string | null;
  mfa_enabled_at?: string | null;
  must_change_password?: boolean;
};
type StaffAction = "password-reset" | "unlock" | "mfa-reset" | "sessions/end";
type Kiosk = {
  id: string;
  label: string;
  /** "device": a paired kiosk screen; "mobile": a phone check-in QR poster. */
  kind?: "device" | "mobile";
  is_active: boolean;
  last_seen_at: string | null;
  paired_at?: string | null;
  created_at?: string | null;
  activation_pending?: boolean;
  activation_code_expires_at?: string | null;
  registrations_today?: number;
  registrations_total?: number;
};
type Page<T> = { items: T[]; next_cursor: string | null };
type Activation = { kiosk: string; code: string; expiresAt: string | null } | null;
/** A phone check-in QR: url is null when this PC's Wi-Fi address is unknown. */
type CheckinQr = { id: string; label: string; path: string; url: string | null };
type Filters = { search: string; role: string; status: string };
type Pending = {
  action: "deactivate" | "delete" | "replace";
  resource: Resource;
  item: { id: string; is_active: boolean };
  name: string;
};

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

function isLocked(item: { locked_until?: string | null }) {
  return Boolean(item.locked_until && new Date(item.locked_until).getTime() > Date.now());
}

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
  const [checkinQr, setCheckinQr] = useState<CheckinQr | null>(null);
  const [filters, setFilters] = useState<Record<Resource, Filters>>({
    users: NO_FILTERS,
    kiosks: NO_FILTERS,
    departments: NO_FILTERS,
  });
  const [pending, setPending] = useState<Pending | null>(null);
  // A temporary password is shown once, right after the reset.
  const [temporary, setTemporary] = useState<{ name: string; password: string } | null>(null);
  const [editing, setEditing] = useState<StaffRecord | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [copied, setCopied] = useState<"code" | "link" | "checkin" | null>(null);
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
          "Idempotency-Key": newRequestId(),
        },
        body: JSON.stringify(payload),
      })) as {
        id: string;
        kind?: Kiosk["kind"];
        activation_code?: string;
        activation_code_expires_at?: string | null;
        label?: string;
      };
      formElement.reset();
      setShowPassword(false);
      if (created.kind === "mobile") {
        setNotice(t("adminCreated"));
        await load("kiosks");
        await showCheckinQr(created);
      } else if (created.activation_code) {
        setCheckinQr(null);
        setCopied(null);
        setActivation({
          kiosk: created.label ?? t("newKiosk"),
          code: created.activation_code,
          expiresAt: created.activation_code_expires_at ?? null,
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
          "Idempotency-Key": newRequestId(),
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

  /** Account help for a staff member: reset password, unlock, reset two-step, sign out. */
  async function staffAction(item: StaffRecord, action: StaffAction) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = (await request(`/admin/users/${item.id}/${action}`, {
        method: "POST",
        headers: { "Idempotency-Key": newRequestId() },
      })) as { temporary_password?: string };
      if (result?.temporary_password) {
        setTemporary({ name: item.name, password: result.temporary_password });
      } else {
        setNotice(
          t(
            action === "unlock"
              ? "staffUnlocked"
              : action === "mfa-reset"
                ? "staffMfaReset"
                : "staffSignedOut",
            { name: item.name },
          ),
        );
      }
      await load("users");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  async function saveEdit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    const form = new FormData(event.currentTarget);
    const changes: Record<string, string | null> = {};
    const name = String(form.get("name") ?? "").trim();
    const email = String(form.get("email") ?? "").trim();
    const department = String(form.get("department_id") ?? "");
    if (name && name !== editing.name) changes.name = name;
    if (email && email !== editing.email) changes.email = email;
    if ((department || null) !== editing.department_id) changes.department_id = department || null;
    if (Object.keys(changes).length === 0) {
      setEditing(null);
      return;
    }
    setSaving(true);
    setError("");
    try {
      await request(`/admin/users/${editing.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "Idempotency-Key": newRequestId() },
        body: JSON.stringify(changes),
      });
      setNotice(t("staffUpdated", { name: name || editing.name }));
      setEditing(null);
      await load("users");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  /** Deactivating asks first; activating again is harmless and happens at once. */
  function requestToggle(item: { id: string; is_active: boolean }, name: string) {
    if (item.is_active) setPending({ action: "deactivate", resource, item, name });
    else void toggle(resource, item);
  }

  async function remove(target: Resource, item: { id: string }) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await request(`/admin/${target}/${item.id}`, {
        method: "DELETE",
        headers: { "Idempotency-Key": newRequestId() },
      });
      if (target === "kiosks") {
        setActivation(null);
        setCheckinQr((current) => (current?.id === item.id ? null : current));
      }
      setNotice(t("adminDeleted"));
      await load(target);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  /** A fresh one-time code for an existing kiosk: same kiosk, same history, new device. */
  async function issueCode(kiosk: Kiosk) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const issued = (await request(`/admin/kiosks/${kiosk.id}/activation-code`, {
        method: "POST",
        headers: { "Idempotency-Key": newRequestId() },
      })) as { activation_code?: string; activation_code_expires_at?: string; label?: string };
      if (issued.activation_code) {
        setCopied(null);
        setCheckinQr(null);
        setActivation({
          kiosk: issued.label ?? kiosk.label,
          code: issued.activation_code,
          expiresAt: issued.activation_code_expires_at ?? null,
        });
      }
      await load("kiosks");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  /** Show a phone check-in point's QR; the same QR can be shown and printed any time. */
  async function showCheckinQr(point: { id: string }) {
    setSaving(true);
    setError("");
    try {
      const link = (await request(`/admin/kiosks/${point.id}/checkin-link`)) as CheckinQr;
      setCopied(null);
      setActivation(null);
      setCheckinQr(link);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  /** A new QR for a check-in point: posters with the old one stop working at once. */
  async function replaceCheckinQr(point: { id: string }) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const link = (await request(`/admin/kiosks/${point.id}/checkin-link`, {
        method: "POST",
        headers: { "Idempotency-Key": newRequestId() },
      })) as CheckinQr;
      setCopied(null);
      setCheckinQr(link);
      setNotice(t("checkinQrReplaced"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("adminRequestError"));
    } finally {
      setSaving(false);
    }
  }

  const activationLink = (code: string) =>
    `${window.location.origin}/kiosk?activate=${encodeURIComponent(code)}`;

  async function copy(kind: "code" | "link" | "checkin", value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
    } catch {
      // Clipboard blocked (e.g. plain http on another device): the code is still selectable.
      setError(t("copyFailed"));
    }
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
  // Only kiosk screens report in; printed phone check-in QRs are never "online".
  const activeKiosks = kiosks.filter((kiosk) => kiosk.is_active && kiosk.kind !== "mobile");
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
  const rowActions = (
    item: { id: string; is_active: boolean },
    name: string,
    options: { self?: boolean; kiosk?: Kiosk; staff?: StaffRecord } = {},
  ) => (
    <span className="adm-actions">
      {toggleButton(item, name, options.self)}
      <Popover
        label={<MoreHorizontal aria-hidden="true" size={16} />}
        ariaLabel={t("moreActionsFor", { name })}
        triggerClassName="adm-more"
        panelClassName="adm-menu"
        align="end"
      >
        {(close) => (
          <>
            {options.kiosk?.kind === "mobile" && (
              <>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => {
                    close();
                    void showCheckinQr(item);
                  }}
                >
                  <QrCode aria-hidden="true" size={15} /> {t("showCheckinQr")}
                </button>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => {
                    close();
                    setPending({ action: "replace", resource, item, name });
                  }}
                >
                  <RefreshCw aria-hidden="true" size={15} /> {t("replaceCheckinQr")}
                </button>
              </>
            )}
            {options.staff && (
              <>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => {
                    close();
                    setEditing(options.staff ?? null);
                  }}
                >
                  <Pencil aria-hidden="true" size={15} /> {t("editStaff")}
                </button>
                {!options.self && (
                  <button
                    type="button"
                    disabled={saving || !item.is_active}
                    onClick={() => {
                      close();
                      void staffAction(options.staff as StaffRecord, "password-reset");
                    }}
                  >
                    <KeyRound aria-hidden="true" size={15} /> {t("resetPassword")}
                  </button>
                )}
                {isLocked(options.staff) && (
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => {
                      close();
                      void staffAction(options.staff as StaffRecord, "unlock");
                    }}
                  >
                    <LockOpen aria-hidden="true" size={15} /> {t("unlockAccount")}
                  </button>
                )}
                {options.staff.mfa_enabled_at && !options.self && (
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => {
                      close();
                      void staffAction(options.staff as StaffRecord, "mfa-reset");
                    }}
                  >
                    <ShieldOff aria-hidden="true" size={15} /> {t("resetMfa")}
                  </button>
                )}
                {!options.self && (
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => {
                      close();
                      void staffAction(options.staff as StaffRecord, "sessions/end");
                    }}
                  >
                    <LogOut aria-hidden="true" size={15} /> {t("signOutEverywhere")}
                  </button>
                )}
              </>
            )}
            {options.kiosk && options.kiosk.kind !== "mobile" && (
              <button
                type="button"
                disabled={saving || !item.is_active}
                title={item.is_active ? undefined : t("newCodeNeedsActive")}
                onClick={() => {
                  close();
                  void issueCode(options.kiosk as Kiosk);
                }}
              >
                <KeyRound aria-hidden="true" size={15} /> {t("newActivationCode")}
              </button>
            )}
            <button
              type="button"
              className="is-danger"
              disabled={saving || options.self}
              title={options.self ? t("cannotDeleteSelf") : undefined}
              onClick={() => {
                close();
                setPending({ action: "delete", resource, item, name });
              }}
            >
              <Trash2 aria-hidden="true" size={15} /> {t("deleteRecord")}
            </button>
          </>
        )}
      </Popover>
    </span>
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
          <td data-label={t("statusColumn")}>
            {statusPill(item.is_active)}
            {isLocked(item) && <span className="adm-status adm-flag">{t("lockedFlag")}</span>}
            {item.mfa_enabled_at && <span className="adm-status adm-flag">{t("mfaFlag")}</span>}
            {item.must_change_password && (
              <span className="adm-status adm-flag">{t("mustChangeFlag")}</span>
            )}
          </td>
          <td className="adm-col-action">{rowActions(item, item.name, { self, staff: item })}</td>
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
              {item.kind === "mobile" ? <Smartphone size={16} /> : <Monitor size={16} />}
            </span>
            <span className="adm-person-text">
              <strong>{item.label}</strong>
              <span title={item.id}>{t("kioskId", { id: item.id.slice(-8) })}</span>
              <span>
                {t("kioskRegistrations", {
                  today: item.registrations_today ?? 0,
                  total: item.registrations_total ?? 0,
                })}
              </span>
            </span>
          </span>
        </td>
        <td data-label={t("kioskAdded")}>
          {item.created_at ? dateOnly.format(new Date(item.created_at)) : "—"}
        </td>
        <td data-label={t("connectionColumn")}>
          {item.kind === "mobile" ? (
            // A printed QR has no device to be online; show when patients last used it.
            <span className="adm-connection">
              <span className={`adm-conn adm-conn--${item.is_active ? "unseen" : "off"}`}>
                {t("kioskKindMobile")}
              </span>
              <span className="adm-muted">
                {item.last_seen_at
                  ? t("checkinLastUsed", { when: ago(item.last_seen_at) })
                  : t("checkinNeverUsed")}
              </span>
            </span>
          ) : (() => {
            const state = connectionOf(item, now);
            return (
              <span className="adm-connection">
                <span className={`adm-conn adm-conn--${state}`}>{t(`kioskConn_${state}`)}</span>
                {item.last_seen_at && state !== "online" && (
                  <span className="adm-muted" title={dateTime.format(new Date(item.last_seen_at))}>
                    {t("kioskLastSeenAt", { when: ago(item.last_seen_at) })}
                  </span>
                )}
                {item.activation_pending && (
                  <span className="adm-muted">
                    {item.activation_code_expires_at
                      ? t("kioskCodePendingUntil", {
                          when: dateTime.format(new Date(item.activation_code_expires_at)),
                        })
                      : t("kioskCodePending")}
                  </span>
                )}
              </span>
            );
          })()}
        </td>
        <td data-label={t("statusColumn")}>{statusPill(item.is_active)}</td>
        <td className="adm-col-action">{rowActions(item, item.label, { kiosk: item })}</td>
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
        <td className="adm-col-action">{rowActions(item, item.name_en)}</td>
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
          {activation && resource === "kiosks" && (
            <aside className="adm-activation" aria-live="polite">
              <h3>
                <KeyRound aria-hidden="true" size={17} /> {t("activationCodeTitle")}
              </h3>
              <p>{t("activationCodeOnce", { kiosk: activation.kiosk })}</p>
              <code>{activation.code}</code>
              {activation.expiresAt && (
                <p>{t("activationCodeExpires", { when: dateTime.format(new Date(activation.expiresAt)) })}</p>
              )}
              <div className="adm-activation-actions">
                <button type="button" onClick={() => void copy("code", activation.code)}>
                  {copied === "code" ? <Check aria-hidden="true" size={15} /> : <Copy aria-hidden="true" size={15} />}
                  {copied === "code" ? t("copied") : t("copyCode")}
                </button>
                <button type="button" onClick={() => void copy("link", activationLink(activation.code))}>
                  {copied === "link" ? <Check aria-hidden="true" size={15} /> : <Link2 aria-hidden="true" size={15} />}
                  {copied === "link" ? t("copied") : t("copySetupLink")}
                </button>
                <button type="button" onClick={() => setActivation(null)}>
                  {t("dismissActivation")}
                </button>
              </div>
              <p>{t("activationCodeHowTo")}</p>
            </aside>
          )}
          {checkinQr && resource === "kiosks" && (
            <aside className="adm-activation adm-checkin-qr" aria-live="polite">
              <h3>
                <QrCode aria-hidden="true" size={17} /> {t("checkinQrTitle")}
              </h3>
              <p>{t("checkinQrFor", { label: checkinQr.label })}</p>
              {checkinQr.url ? (
                <>
                  <div className="adm-checkin-qr-code">
                    <QRCodeSVG
                      value={checkinQr.url}
                      size={220}
                      level="M"
                      marginSize={2}
                      title={t("checkinQrTitle")}
                    />
                  </div>
                  <code>{checkinQr.url}</code>
                  <p className="no-print">{t("checkinQrTest")}</p>
                </>
              ) : (
                <p className="adm-banner adm-banner--error" role="alert">
                  {t("checkinQrNoAddress")}
                </p>
              )}
              <div className="adm-activation-actions no-print">
                {checkinQr.url && (
                  <>
                    <button type="button" onClick={() => window.print()}>
                      <Printer aria-hidden="true" size={15} /> {t("printCheckinQr")}
                    </button>
                    <button
                      type="button"
                      onClick={() => void copy("checkin", checkinQr.url ?? "")}
                    >
                      {copied === "checkin" ? (
                        <Check aria-hidden="true" size={15} />
                      ) : (
                        <Link2 aria-hidden="true" size={15} />
                      )}
                      {copied === "checkin" ? t("copied") : t("copyCheckinLink")}
                    </button>
                  </>
                )}
                <button type="button" onClick={() => setCheckinQr(null)}>
                  {t("dismissActivation")}
                </button>
              </div>
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
              <>
                <label>
                  <span>{t("kioskKind")}</span>
                  <select name="kind" defaultValue="device">
                    <option value="device">{t("kioskKindDevice")}</option>
                    <option value="mobile">{t("kioskKindMobile")}</option>
                  </select>
                  <small>{t("kioskKindHint")}</small>
                </label>
                <label>
                  <span>
                    {t("kioskLabel")} <em aria-hidden="true">*</em>
                  </span>
                  <input name="label" required maxLength={100} placeholder={t("kioskLabelPlaceholder")} />
                  <small>{t("kioskLabelHint")}</small>
                </label>
              </>
            )}
            <p className="adm-required">
              <em aria-hidden="true">*</em> {t("requiredFields")}
            </p>
            <button type="submit" className="adm-submit" disabled={saving}>
              <PlusCircle aria-hidden="true" size={17} />
              {saving ? t("savingAdmin") : t(`adminCreate${cap(resource)}`)}
            </button>
          </form>
          {resource === "departments" && <QueueSettingsCard />}
          {resource === "departments" && <InterviewSettingsCard />}
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

      {temporary && (
        <div className="rp-dialog-backdrop">
          <section
            className="rp-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={`${baseId}-temporary`}
          >
            <h3 id={`${baseId}-temporary`}>{t("temporaryPasswordTitle", { name: temporary.name })}</h3>
            <p>{t("temporaryPasswordHint")}</p>
            <p className="adm-code">
              <code>{temporary.password}</code>
            </p>
            <div className="rp-dialog-actions">
              <button type="button" autoFocus onClick={() => setTemporary(null)}>
                {t("temporaryPasswordDone")}
              </button>
            </div>
          </section>
        </div>
      )}

      {editing && (
        <div
          className="rp-dialog-backdrop"
          onKeyDown={(event) => {
            if (event.key === "Escape") setEditing(null);
          }}
        >
          <section
            className="rp-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`${baseId}-edit`}
          >
            <h3 id={`${baseId}-edit`}>{t("editStaffTitle", { name: editing.name })}</h3>
            <form className="acct-form" onSubmit={saveEdit}>
              <label htmlFor={`${baseId}-edit-name`}>{t("staffName")}</label>
              <input id={`${baseId}-edit-name`} name="name" required defaultValue={editing.name} />
              <label htmlFor={`${baseId}-edit-email`}>{t("email")}</label>
              <input
                id={`${baseId}-edit-email`}
                name="email"
                type="email"
                required
                defaultValue={editing.email}
              />
              <label htmlFor={`${baseId}-edit-department`}>{t("department")}</label>
              <select
                id={`${baseId}-edit-department`}
                name="department_id"
                defaultValue={editing.department_id ?? ""}
              >
                <option value="">{t("noDepartment")}</option>
                {departments
                  .filter((department) => department.is_active || department.id === editing.department_id)
                  .map((department) => (
                    <option key={department.id} value={department.id}>
                      {departmentLabel(department)}
                    </option>
                  ))}
              </select>
              <div className="rp-dialog-actions">
                <button type="button" onClick={() => setEditing(null)}>
                  {t("cancelEdit")}
                </button>
                <button type="submit" className="is-primary" disabled={saving}>
                  {t("saveChanges")}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

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
            <h3 id={`${baseId}-confirm`}>
              {pending.action === "delete"
                ? t("confirmDeleteTitle", { name: pending.name })
                : pending.action === "replace"
                  ? t("confirmReplaceQrTitle", { name: pending.name })
                  : t("confirmDeactivateTitle", { name: pending.name })}
            </h3>
            <p id={`${baseId}-confirm-text`}>
              {pending.action === "delete"
                ? t(`confirmDelete${cap(pending.resource)}`)
                : pending.action === "replace"
                  ? t("confirmReplaceQr")
                  : pending.resource === "users"
                    ? t("confirmDeactivateUser")
                    : t("confirmDeactivateOther")}
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
                  if (target.action === "delete") void remove(target.resource, target.item);
                  else if (target.action === "replace") void replaceCheckinQr(target.item);
                  else void toggle(target.resource, target.item);
                }}
              >
                {pending.action === "delete"
                  ? t("deleteRecord")
                  : pending.action === "replace"
                    ? t("replaceCheckinQr")
                    : t("deactivate")}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
