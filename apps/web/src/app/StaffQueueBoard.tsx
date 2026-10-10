import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, FileText, Hand, MoreHorizontal, RefreshCw, Users } from "lucide-react";
import { ConnectionIndicator, Popover, UrgencyBadge } from "../components/ui";
import { TriageWhy } from "./TriageWhy";
import { apiSocketUrl } from "../lib/api";
import { staffAuth } from "../lib/auth";
import {
  applyQueueOperations,
  sequenceAction,
  sortQueue,
  waitMinutes,
  type QueueItem,
  type QueueOperation,
  type QueueStatus,
} from "./queueBoardModel";

type QueueDepartment = { id: string; code: string; name: string };
type QueueMessage = {
  type?: string;
  seq?: number;
  items?: QueueItem[];
  ops?: QueueOperation[];
};
type StaffAlert = {
  id: string;
  visit_id: string;
  rule_id: string | null;
  level: string;
  evidence: string | null;
  reason_en?: string;
  token_label?: string | null;
  /** "llm" marks an AI suggestion a nurse must confirm. */
  source?: string;
};
type ConnectionState = "live" | "reconnecting" | "offline";

const PAGE_SIZE = 50;
const SOUND_PREFERENCE_KEY = "opd-staff-alert-sound";
type QueueSnapshot = {
  items: QueueItem[];
  departments: QueueDepartment[];
  nextCursor: string | null;
  expiresAt: number;
};
const queueSnapshots = new Map<string, QueueSnapshot>();
const preferredDepartmentByHospital = new Map<string, string>();

function queueCacheKey(
  userScope: string,
  departmentId: string,
  status: QueueStatus,
) {
  return `${userScope}|${departmentId}|${status}`;
}

function getQueueSnapshot(key: string): QueueSnapshot | undefined {
  const snapshot = queueSnapshots.get(key);
  if (snapshot && snapshot.expiresAt <= Date.now()) {
    queueSnapshots.delete(key);
    return undefined;
  }
  return snapshot;
}

function initialSoundPreference(): boolean {
  try {
    return window.localStorage.getItem(SOUND_PREFERENCE_KEY) === "enabled";
  } catch {
    return false;
  }
}

const STATUS_LABELS: Record<string, string> = {
  queued: "Waiting",
  called: "Called",
  in_consult: "In consultation",
};

const ROUTE_LABELS: Record<string, string> = {
  NURSE_TRIAGE: "Nurse triage",
  GEN: "General Medicine",
  MED: "General Medicine",
  PED: "Pediatrics",
};

function statusLabel(status: string) {
  return STATUS_LABELS[status] ?? status.replace(/_/g, " ");
}

function routeLabel(code: string | null | undefined) {
  if (!code) return "Not decided";
  return ROUTE_LABELS[code] ?? code.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

function sexLabel(sex: string | null | undefined) {
  if (!sex) return "Sex —";
  return sex.charAt(0).toUpperCase() + sex.slice(1);
}

function sentenceCase(text: string | null | undefined) {
  const value = (text ?? "").trim();
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function formatWait(minutes: number | null) {
  if (minutes === null) return "—";
  if (minutes < 1) return "Just arrived";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} m` : `${hours} h`;
}

export function StaffQueueBoard() {
  const { t } = useTranslation("staff");
  const hospitalId = staffAuth.user?.hospital_id ?? "";
  const userScope = `${hospitalId}:${staffAuth.user?.id ?? ""}`;
  const [departmentId, setDepartmentId] = useState(
    () => preferredDepartmentByHospital.get(userScope) ?? "",
  );
  const [statusFilter, setStatusFilter] = useState<QueueStatus>("all");
  const initialSnapshot = getQueueSnapshot(
    queueCacheKey(userScope, departmentId, "all"),
  );
  const [items, setItems] = useState<QueueItem[]>(initialSnapshot?.items ?? []);
  const [departments, setDepartments] = useState<QueueDepartment[]>(
    initialSnapshot?.departments ?? [],
  );
  const [nextCursor, setNextCursor] = useState<string | null>(
    initialSnapshot?.nextCursor ?? null,
  );
  const [alerts, setAlerts] = useState<StaffAlert[]>([]);
  const [error, setError] = useState("");
  const [working, setWorking] = useState<string>();
  const [soundEnabled, setSoundEnabled] = useState(initialSoundPreference);
  const [connection, setConnection] = useState<ConnectionState>("reconnecting");
  const [overrideValues, setOverrideValues] = useState<
    Record<string, { urgency: string; reason: string }>
  >({});
  const [routeReasons, setRouteReasons] = useState<Record<string, string>>({});
  const [loadingMore, setLoadingMore] = useState(false);
  const [queueLoaded, setQueueLoaded] = useState(Boolean(initialSnapshot));
  const [now, setNow] = useState(Date.now());
  const [scrollElement, setScrollElement] = useState<HTMLDivElement | null>(
    null,
  );
  const soundRef = useRef(soundEnabled);
  const sequenceRef = useRef(0);
  const loadedIdsRef = useRef(new Set<string>());
  const requestScopeRef = useRef("");
  const scrollRef = useCallback((element: HTMLDivElement | null) => {
    setScrollElement(element);
  }, []);

  useEffect(() => {
    soundRef.current = soundEnabled;
    try {
      window.localStorage.setItem(
        SOUND_PREFERENCE_KEY,
        soundEnabled ? "enabled" : "disabled",
      );
    } catch {
      // Sound remains available for this session if browser storage is disabled.
    }
  }, [soundEnabled]);

  useEffect(() => {
    requestScopeRef.current = `${departmentId}|${statusFilter}`;
  }, [departmentId, statusFilter]);

  const refresh = useCallback(
    async (cursor?: string, append = false) => {
      const requestScope = `${departmentId}|${statusFilter}`;
      const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (departmentId) query.set("department_id", departmentId);
      if (statusFilter !== "all") query.set("status", statusFilter);
      if (cursor) query.set("cursor", cursor);
      const response = await staffAuth.request(`/queue?${query.toString()}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        if (response.status === 401)
          throw new Error("Your staff session expired. Sign in again.");
        if (response.status === 403)
          throw new Error(
            "This staff account cannot access the selected queue.",
          );
        throw new Error(
          `Queue is temporarily unavailable (API ${response.status}).`,
        );
      }
      const body = (await response.json()) as {
        items: QueueItem[];
        next_cursor: string | null;
        departments?: QueueDepartment[];
      };
      if (requestScopeRef.current !== requestScope) return;
      if (body.departments) {
        setDepartments(body.departments);
        if (!departmentId && body.departments[0]) {
          preferredDepartmentByHospital.set(userScope, body.departments[0].id);
          setDepartmentId(body.departments[0].id);
        }
      }
      let pageItems = body.items;
      if (!append)
        loadedIdsRef.current = new Set(body.items.map((item) => item.visit_id));
      else {
        pageItems = body.items.filter(
          (item) => !loadedIdsRef.current.has(item.visit_id),
        );
        body.items.forEach((item) => loadedIdsRef.current.add(item.visit_id));
      }
      setItems((current) => (append ? [...current, ...pageItems] : body.items));
      setNextCursor(body.next_cursor);
      setQueueLoaded(true);
      setError("");
      if (!append && hospitalId && departmentId) {
        queueSnapshots.set(
          queueCacheKey(userScope, departmentId, statusFilter),
          {
            items: body.items,
            departments: body.departments ?? [],
            nextCursor: body.next_cursor,
            expiresAt: Date.now() + 30_000,
          },
        );
      }
    },
    [departmentId, statusFilter, hospitalId, userScope],
  );

  const refreshAlerts = useCallback(async () => {
    const query = new URLSearchParams({ acknowledged: "false" });
    if (departmentId) query.set("department_id", departmentId);
    const response = await staffAuth.request(`/alerts?${query.toString()}`, {
      cache: "no-store",
    });
    if (!response.ok) return;
    const body = (await response.json()) as { items: StaffAlert[] };
    setAlerts(body.items);
  }, [departmentId]);

  const chime = useCallback(() => {
    if (!soundRef.current) return;
    const AudioContextClass = window.AudioContext;
    if (!AudioContextClass) return;
    const context = new AudioContextClass();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = 880;
    gain.gain.setValueAtTime(0.12, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.35);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.35);
    oscillator.onended = () => void context.close();
  }, []);

  useEffect(() => {
    if (hospitalId && departmentId) {
      preferredDepartmentByHospital.set(userScope, departmentId);
      const cached = getQueueSnapshot(
        queueCacheKey(userScope, departmentId, statusFilter),
      );
      if (cached) {
        setItems(cached.items);
        setDepartments(cached.departments);
        setNextCursor(cached.nextCursor);
        setQueueLoaded(true);
        loadedIdsRef.current = new Set(
          cached.items.map((item) => item.visit_id),
        );
      } else {
        setItems([]);
        setNextCursor(null);
        setQueueLoaded(false);
      }
    }
    let disposed = false;
    let socket: WebSocket | undefined;
    let alertSocket: WebSocket | undefined;
    let reconnectTimer: number | undefined;
    let alertReconnectTimer: number | undefined;
    const poll = window.setInterval(() => {
      void refresh().catch((cause) => {
        setConnection("offline");
        setError(
          cause instanceof Error
            ? `${cause.message} Reconnecting…`
            : "Queue is temporarily unavailable. Reconnecting…",
        );
      });
      void refreshAlerts();
    }, 15_000);
    const connect = () => {
      if (disposed || !departmentId) return;
      const token = staffAuth.accessToken;
      if (!token) {
        setConnection("offline");
        return;
      }
      setConnection("reconnecting");
      const api = apiSocketUrl("/ws/staff/queue");
      api.searchParams.set("department_id", departmentId);
      socket = new WebSocket(api);
      socket.onopen = () => {
        setConnection("live");
        socket?.send(JSON.stringify({ type: "auth", token }));
      };
      socket.onmessage = (event) => {
        let message: QueueMessage & StaffAlert;
        try {
          message = JSON.parse(String(event.data)) as QueueMessage & StaffAlert;
        } catch {
          return;
        }
        if (message.type === "ping") {
          socket?.send(JSON.stringify({ type: "pong" }));
          return;
        }
        if (
          message.type === "queue.snapshot" &&
          typeof message.seq === "number"
        ) {
          sequenceRef.current = message.seq;
          return;
        }
        if (message.type === "queue.delta" && typeof message.seq === "number") {
          const sequence = sequenceAction(sequenceRef.current, message.seq);
          if (sequence === "ignore") return;
          if (sequence === "resync") {
            sequenceRef.current = message.seq;
            void refresh().catch(() => setConnection("offline"));
            return;
          }
          sequenceRef.current = message.seq;
          const operations = message.ops ?? [];
          const needsResync = operations.some(
            (operation) =>
              operation.op === "remove" ||
              !loadedIdsRef.current.has(operation.item.visit_id),
          );
          setItems(
            (current) =>
              applyQueueOperations(current, operations, statusFilter).items,
          );
          if (needsResync) void refresh().catch(() => setConnection("offline"));
          return;
        }
        if (
          message.type === "alert.urgent" ||
          message.type === "alert.escalated"
        ) {
          chime();
          setAlerts((current) =>
            current.some((alert) => alert.id === message.id)
              ? current
              : [message, ...current],
          );
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        setConnection("reconnecting");
        reconnectTimer = window.setTimeout(connect, 2000);
      };
      socket.onerror = () => setConnection("offline");
    };
    const connectAlerts = () => {
      if (disposed) return;
      const token = staffAuth.accessToken;
      if (!token) return;
      const api = apiSocketUrl("/ws/staff/queue");
      api.searchParams.set("alerts_only", "true");
      if (departmentId) api.searchParams.set("department_id", departmentId);
      alertSocket = new WebSocket(api);
      alertSocket.onopen = () =>
        alertSocket?.send(JSON.stringify({ type: "auth", token }));
      alertSocket.onmessage = (event) => {
        let message: StaffAlert & { type?: string };
        try {
          message = JSON.parse(String(event.data)) as StaffAlert & {
            type?: string;
          };
        } catch {
          return;
        }
        if (message.type === "ping")
          alertSocket?.send(JSON.stringify({ type: "pong" }));
        if (
          message.type === "alert.urgent" ||
          message.type === "alert.escalated"
        ) {
          chime();
          setAlerts((current) =>
            current.some((alert) => alert.id === message.id)
              ? current
              : [message, ...current],
          );
        }
      };
      alertSocket.onclose = () => {
        if (!disposed)
          alertReconnectTimer = window.setTimeout(connectAlerts, 2000);
      };
    };
    void refresh()
      .then(() => {
        if (!disposed) void refreshAlerts();
      })
      .catch((cause) => {
        setConnection("offline");
        setError(
          cause instanceof Error
            ? cause.message
            : "Queue is temporarily unavailable.",
        );
      });
    if (departmentId) {
      connect();
      connectAlerts();
    }
    return () => {
      disposed = true;
      window.clearInterval(poll);
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (alertReconnectTimer) window.clearTimeout(alertReconnectTimer);
      socket?.close();
      alertSocket?.close();
    };
  }, [
    departmentId,
    statusFilter,
    refresh,
    refreshAlerts,
    chime,
    hospitalId,
    userScope,
  ]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  async function acknowledge(alertId: string) {
    const response = await staffAuth.request(`/alerts/${alertId}/ack`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    });
    if (!response.ok) {
      setError("Could not acknowledge this alert. Refresh and try again.");
      return;
    }
    setAlerts((current) => current.filter((alert) => alert.id !== alertId));
  }

  async function action(
    visitId: string,
    name: "call" | "start" | "complete" | "no-show",
  ) {
    setWorking(visitId);
    setError("");
    try {
      const response = await staffAuth.request(`/queue/${visitId}/${name}`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      if (!response.ok)
        throw new Error("That queue action is no longer available.");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Queue action failed.");
    } finally {
      setWorking(undefined);
    }
  }

  async function override(visitId: string) {
    const value = overrideValues[visitId];
    if (!value || value.reason.trim().length < 5) return;
    setWorking(visitId);
    setError("");
    try {
      const response = await staffAuth.request(`/queue/${visitId}/override`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({
          urgency: value.urgency,
          reason: value.reason.trim(),
        }),
      });
      if (!response.ok) throw new Error("Could not update the queue priority.");
      setOverrideValues((current) => ({
        ...current,
        [visitId]: { ...value, reason: "" },
      }));
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Queue override failed.",
      );
    } finally {
      setWorking(undefined);
    }
  }

  async function confirmRoute(visitId: string) {
    const reason = routeReasons[visitId]?.trim() ?? "";
    if (reason.length < 5) return;
    setWorking(visitId);
    setError("");
    try {
      const response = await staffAuth.request(
        `/queue/${visitId}/routing/confirm`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: JSON.stringify({ reason }),
        },
      );
      if (!response.ok) throw new Error("Could not confirm this route.");
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Route confirmation failed.",
      );
    } finally {
      setWorking(undefined);
    }
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      await refresh(nextCursor, true);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not load more patients.",
      );
    } finally {
      setLoadingMore(false);
    }
  }

  const canOverride =
    staffAuth.user?.role === "nurse" || staffAuth.user?.role === "admin";
  const canOpenCase =
    staffAuth.user?.role === "doctor" || staffAuth.user?.role === "admin";
  const orderedItems = useMemo(() => sortQueue(items), [items]);
  // The virtualizer exposes imperative measurements; React Compiler intentionally skips it.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: orderedItems.length,
    getScrollElement: () => scrollElement,
    estimateSize: () => 88,
    overscan: 8,
  });

  const counts = {
    queued: items.filter((item) => item.status === "queued").length,
    called: items.filter((item) => item.status === "called").length,
    in_consult: items.filter((item) => item.status === "in_consult").length,
    attention: items.filter((item) => item.urgency !== "routine").length,
  };
  const activeDepartment = departments.find((department) => department.id === departmentId);

  return (
    <section className="staff-queue qb" aria-label="Live patient queue">
      <header className="qb-head">
        <div>
          <h2>
            Queue
            {activeDepartment && <span className="qb-dept-name"> · {activeDepartment.name}</span>}
          </h2>
          <p>Today's waiting room, in calling order</p>
        </div>
        <div className="qb-head-actions">
          <ConnectionIndicator state={connection} />
          <button type="button" className="qb-btn qb-btn--ghost" onClick={() => void refresh()}>
            <RefreshCw aria-hidden="true" size={16} /> Refresh
          </button>
        </div>
      </header>

      <div className="qb-stats" aria-label="Queue summary">
        {[
          ["Waiting", counts.queued, "qb-stat--wait"],
          ["Called", counts.called, "qb-stat--called"],
          ["In consultation", counts.in_consult, "qb-stat--consult"],
          ["Priority or urgent", counts.attention, "qb-stat--attention"],
        ].map(([label, value, tone]) => (
          <div className={`qb-stat ${tone}`} key={label as string}>
            <strong>{value}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <div className="qb-toolbar">
        <div className="qb-tabs" role="group" aria-label="Department">
          {departments.map((department) => (
            <button
              type="button"
              key={department.id}
              aria-pressed={department.id === departmentId}
              onClick={(event) => {
                // The tab row scrolls sideways; keep the chosen tab fully in view.
                event.currentTarget.scrollIntoView?.({ block: "nearest", inline: "nearest" });
                if (department.id === departmentId) return;
                sequenceRef.current = 0;
                setDepartmentId(department.id);
              }}
            >
              {department.name}
            </button>
          ))}
        </div>
        <div className="qb-toolbar-right">
          <label className="qb-select">
            Status
            <select
              aria-label="Status"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as QueueStatus)}
            >
              <option value="all">All active</option>
              <option value="queued">Waiting</option>
              <option value="called">Called</option>
              <option value="in_consult">In consultation</option>
            </select>
          </label>
          <label className="qb-toggle">
            <input
              type="checkbox"
              checked={soundEnabled}
              onChange={(event) => setSoundEnabled(event.target.checked)}
            />
            Alert sound
          </label>
        </div>
      </div>

      {alerts.length > 1 && (
        <p className="qb-alerts-title">{alerts.length} alerts need attention · newest first</p>
      )}
      {alerts.length > 0 && (
        <section className="qb-alerts" aria-label="Unacknowledged staff alerts" aria-live="assertive">
          {alerts.map((alert) => {
            const help = alert.rule_id === "PATIENT_HELP";
            return (
              <article
                className={`qb-alert ${help ? "qb-alert--help" : alert.level === "priority" ? "qb-alert--priority" : "qb-alert--urgent"}`}
                role="alert"
                key={alert.id}
              >
                {help ? <Hand aria-hidden="true" size={20} /> : <AlertTriangle aria-hidden="true" size={20} />}
                <div className="qb-alert-text">
                  <strong>
                    {help ? t("helpRequested") : alert.level === "priority" ? t("priorityAlert") : t("urgentAlert")}
                    {" · "}
                    {alert.token_label ?? "Patient at kiosk"}
                  </strong>
                  <span>
                    {alert.reason_en ?? alert.rule_id ?? "Review this patient now."}
                    {alert.evidence && !help
                      ? alert.rule_id === "AI_TRIAGE_REVIEW" || alert.source === "llm"
                        ? ` — AI quoted: “${alert.evidence}”`
                        : ` — patient said “${alert.evidence}”`
                      : ""}
                  </span>
                </div>
                <button type="button" className="qb-btn qb-btn--ghost" onClick={() => void acknowledge(alert.id)}>
                  Acknowledge
                </button>
              </article>
            );
          })}
        </section>
      )}

      {error && <p className="qb-error" role="alert">{error}</p>}
      {!queueLoaded && !error && <p role="status">{t("loadingQueue")}</p>}
      {queueLoaded && departments.length === 0 && !error && <p role="status">{t("noQueueDepartments")}</p>}
      {departments.length > 0 && items.length === 0 && (
        <div className="qb-empty" role="status">
          <Users aria-hidden="true" size={28} />
          <p>{t("noQueueMatches")}</p>
        </div>
      )}

      {items.length > 0 && (
        <div className="qb-table">
          <div className="qb-row qb-row--head" aria-hidden="true">
            <span>Token</span>
            <span>Patient</span>
            <span>Complaint</span>
            <span>Urgency</span>
            <span>Waiting</span>
            <span>Status</span>
            <span className="qb-col-actions">Actions</span>
          </div>
          <div className="staff-queue-viewport qb-viewport" ref={scrollRef} role="region" aria-label="Queue patients">
            <div className="staff-queue-virtual" style={{ height: `${virtualizer.getTotalSize()}px` }}>
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const item = orderedItems[virtualRow.index];
                const busy = working === item.visit_id || Boolean(error);
                const nextAction =
                  item.status === "queued"
                    ? { name: "call" as const, label: "Call" }
                    : item.status === "called"
                      ? { name: "start" as const, label: "Start" }
                      : { name: "complete" as const, label: "Complete" };
                const caseLink = `/staff/consultations?visit_id=${item.visit_id}&status=${item.status}`;
                return (
                  <article
                    className={`qb-item qb-item--${item.urgency}`}
                    key={item.visit_id}
                    data-index={virtualRow.index}
                    ref={virtualizer.measureElement}
                    style={{ transform: `translateY(${virtualRow.start}px)` }}
                  >
                    <div className="qb-card">
                    <div className="qb-row">
                      <div className="qb-token">
                        <strong>{item.token}</strong>
                        <span>#{item.position} in line</span>
                      </div>
                      <div className="qb-patient">
                        {item.age_years === null ? "Age —" : `${item.age_years} y`} · {sexLabel(item.sex)}
                      </div>
                      <div className="qb-complaint" title={item.complaint}>
                        {sentenceCase(item.complaint) || <span className="qb-muted">Not recorded</span>}
                        {item.needs_assistance && (
                          <span className="qb-flag"><Hand aria-hidden="true" size={13} /> {t("patientNeedsAssistance")}</span>
                        )}
                      </div>
                      <div className="qb-urgency">
                        <UrgencyBadge level={item.urgency} />
                        {item.triage && <TriageWhy triage={item.triage} queuedAt={item.queued_at} />}
                      </div>
                      <div className="qb-wait">{formatWait(waitMinutes(item.queued_at, now))}</div>
                      <div>
                        <span className={`qb-status qb-status--${item.status}`}>{statusLabel(item.status)}</span>
                      </div>
                      <div className="qb-col-actions qb-actions">
                        {canOpenCase && (
                          <Link className="qb-btn qb-btn--ghost" to={caseLink}>
                            <FileText aria-hidden="true" size={16} /> {t("openCaseSheet")}
                          </Link>
                        )}
                        <button
                          type="button"
                          className="qb-btn qb-btn--primary"
                          disabled={busy}
                          onClick={() => void action(item.visit_id, nextAction.name)}
                        >
                          {nextAction.label}
                        </button>
                        {(item.status !== "in_consult" || canOverride) && (
                          <Popover
                            align="end"
                            triggerClassName="qb-more"
                            panelClassName="qb-menu"
                            ariaLabel={`More actions for ${item.token}`}
                            label={<MoreHorizontal aria-hidden="true" size={18} />}
                          >
                            {(closeMenu) => (
                            <>
                              {(item.status === "queued" || item.status === "called") && (
                                <button
                                  type="button"
                                  className="qb-btn qb-btn--ghost"
                                  disabled={busy}
                                  onClick={() => {
                                    closeMenu();
                                    void action(item.visit_id, "no-show");
                                  }}
                                >
                                  Mark no show
                                </button>
                              )}
                              {canOverride && item.status === "queued" && (
                                <div className="qb-override">
                                  <strong>Change priority</strong>
                                  <label>
                                    Priority
                                    <select
                                      value={overrideValues[item.visit_id]?.urgency ?? item.urgency}
                                      onChange={(event) =>
                                        setOverrideValues((current) => ({
                                          ...current,
                                          [item.visit_id]: {
                                            urgency: event.target.value,
                                            reason: current[item.visit_id]?.reason ?? "",
                                          },
                                        }))
                                      }
                                    >
                                      <option value="routine">Routine</option>
                                      <option value="priority">Priority</option>
                                      <option value="urgent">Urgent</option>
                                    </select>
                                  </label>
                                  <label>
                                    Reason
                                    <input
                                      value={overrideValues[item.visit_id]?.reason ?? ""}
                                      maxLength={500}
                                      minLength={5}
                                      placeholder="At least 5 characters"
                                      onChange={(event) =>
                                        setOverrideValues((current) => ({
                                          ...current,
                                          [item.visit_id]: {
                                            urgency: current[item.visit_id]?.urgency ?? item.urgency,
                                            reason: event.target.value,
                                          },
                                        }))
                                      }
                                    />
                                  </label>
                                  <button
                                    type="button"
                                    className="qb-btn qb-btn--primary"
                                    disabled={busy || (overrideValues[item.visit_id]?.reason.trim().length ?? 0) < 5}
                                    onClick={() => {
                                      closeMenu();
                                      void override(item.visit_id);
                                    }}
                                  >
                                    Apply override
                                  </button>
                                </div>
                              )}
                            </>
                            )}
                          </Popover>
                        )}
                      </div>
                    </div>
                    {item.routing?.needs_nurse_confirmation && (
                      <div className="qb-route">
                        <AlertTriangle aria-hidden="true" size={15} />
                        <span>
                          Department not confirmed — suggested: <b>{routeLabel(item.routing.department_code)}</b>
                        </span>
                        {canOverride ? (
                          <span className="qb-route-form">
                            <input
                              aria-label="Reason"
                              placeholder="Reason (5+ characters)"
                              value={routeReasons[item.visit_id] ?? ""}
                              maxLength={500}
                              minLength={5}
                              onChange={(event) =>
                                setRouteReasons((current) => ({ ...current, [item.visit_id]: event.target.value }))
                              }
                            />
                            <button
                              type="button"
                              className="qb-btn qb-btn--ghost"
                              disabled={busy || (routeReasons[item.visit_id]?.trim().length ?? 0) < 5}
                              onClick={() => void confirmRoute(item.visit_id)}
                            >
                              Confirm department
                            </button>
                          </span>
                        ) : (
                          <span className="qb-muted">A nurse will confirm.</span>
                        )}
                      </div>
                    )}
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
          {nextCursor && (
            <button
              type="button"
              className="qb-btn qb-btn--ghost staff-queue-load-more"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {loadingMore ? "Loading…" : "Load more patients"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
