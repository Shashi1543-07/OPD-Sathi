import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Link, useNavigate } from "react-router-dom";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import {
  AlertTriangle,
  Bell,
  BellOff,
  CheckCircle2,
  Clock3,
  FileText,
  Hand,
  ListOrdered,
  MoreHorizontal,
  PhoneCall,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  Smartphone,
  Stethoscope,
  Undo2,
  UserRound,
  UserX,
  Users,
} from "lucide-react";
import { ConnectionIndicator, Popover, UrgencyBadge } from "../components/ui";
import { TriageWhy } from "./TriageWhy";
import { apiSocketUrl } from "../lib/api";
import { staffAuth } from "../lib/auth";
import { DANGER_TILE_LABELS } from "../lib/dangerTiles";
import { newRequestId } from "../lib/ids";
import {
  applyQueueOperations,
  formatCountdown,
  graceLeft,
  sequenceAction,
  sortQueue,
  waitMinutes,
  type MissedItem,
  type QueueItem,
  type QueueOperation,
  type QueueStatus,
} from "./queueBoardModel";
import { queueProblem } from "./queueProblem";

type QueueAction = "call" | "start" | "complete" | "skip" | "recall" | "return" | "no-show";

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
/** Close code the server uses when a board's sign-in expired while it was open. */
const SIGN_IN_EXPIRED = 4401;
const SOUND_PREFERENCE_KEY = "opd-staff-alert-sound";
const DEFAULT_GRACE_SECONDS = 180;
type QueueSnapshot = {
  items: QueueItem[];
  departments: QueueDepartment[];
  nextCursor: string | null;
  missed: MissedItem[];
  graceSeconds: number;
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

/** Routing codes with a name of their own on the board; others are shown as written. */
const ROUTE_LABELS: Record<string, string> = {
  NURSE_TRIAGE: "qbRouteNurseTriage",
  GEN: "qbRouteGeneralMedicine",
  MED: "qbRouteGeneralMedicine",
  PED: "qbRoutePaediatrics",
};

function statusLabel(status: string, t: TFunction) {
  return t(`queueStatus_${status}`, { defaultValue: status.replace(/_/g, " ") });
}

function routeLabel(code: string | null | undefined, t: TFunction) {
  if (!code) return t("qbRouteUndecided");
  const key = ROUTE_LABELS[code];
  return key
    ? t(key)
    : code.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

function sexLabel(sex: string | null | undefined, t: TFunction) {
  if (!sex) return t("qbSexUnknown");
  return t(`sex_${sex}`, { defaultValue: sex.charAt(0).toUpperCase() + sex.slice(1) });
}

function ageLabel(age: number | null, t: TFunction) {
  return age === null ? t("qbAgeUnknown") : t("yearsShort", { age });
}

function sentenceCase(text: string | null | undefined) {
  const value = (text ?? "").trim();
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function formatWait(minutes: number | null, t: TFunction) {
  if (minutes === null) return "—";
  if (minutes < 1) return t("qbJustArrived");
  if (minutes < 60) return t("qbWaitMinutes", { minutes });
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? t("qbWaitHoursMinutes", { hours, minutes: rest }) : t("qbWaitHours", { hours });
}

export function StaffQueueBoard() {
  const { t, i18n } = useTranslation("staff");
  const navigate = useNavigate();
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
  const [missed, setMissed] = useState<MissedItem[]>(initialSnapshot?.missed ?? []);
  const [graceSeconds, setGraceSeconds] = useState(
    initialSnapshot?.graceSeconds ?? DEFAULT_GRACE_SECONDS,
  );
  const [alerts, setAlerts] = useState<StaffAlert[]>([]);
  const [error, setError] = useState("");
  // A refused action (doctor busy, too early to mark absent) never locks the board.
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
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
        if (response.status === 401) throw new Error(t("qbSessionExpired"));
        if (response.status === 403) throw new Error(t("qbNoAccess"));
        throw new Error(t("qbUnavailableStatus", { status: response.status }));
      }
      const body = (await response.json()) as {
        items: QueueItem[];
        next_cursor: string | null;
        departments?: QueueDepartment[];
        missed?: MissedItem[];
        call_grace_seconds?: number;
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
      if (!append) {
        setMissed(body.missed ?? []);
        setGraceSeconds(body.call_grace_seconds ?? DEFAULT_GRACE_SECONDS);
      }
      setQueueLoaded(true);
      setError("");
      if (!append && hospitalId && departmentId) {
        queueSnapshots.set(
          queueCacheKey(userScope, departmentId, statusFilter),
          {
            items: body.items,
            departments: body.departments ?? [],
            nextCursor: body.next_cursor,
            missed: body.missed ?? [],
            graceSeconds: body.call_grace_seconds ?? DEFAULT_GRACE_SECONDS,
            expiresAt: Date.now() + 30_000,
          },
        );
      }
    },
    [departmentId, statusFilter, hospitalId, userScope, t],
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
        setMissed(cached.missed);
        setGraceSeconds(cached.graceSeconds);
        setQueueLoaded(true);
        loadedIdsRef.current = new Set(
          cached.items.map((item) => item.visit_id),
        );
      } else {
        setItems([]);
        setNextCursor(null);
        setMissed([]);
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
          t("qbReconnecting", {
            message: cause instanceof Error ? cause.message : t("qbUnavailable"),
          }),
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
      socket.onclose = (event) => {
        if (disposed) return;
        setConnection("reconnecting");
        if (event.code === SIGN_IN_EXPIRED) {
          // The board stayed open past the 10-minute sign-in: renew it, then reconnect.
          void staffAuth.refresh().finally(() => {
            if (!disposed) reconnectTimer = window.setTimeout(connect, 250);
          });
          return;
        }
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
      alertSocket.onclose = (event) => {
        if (disposed) return;
        if (event.code === SIGN_IN_EXPIRED) {
          void staffAuth.refresh().finally(() => {
            if (!disposed) alertReconnectTimer = window.setTimeout(connectAlerts, 250);
          });
          return;
        }
        alertReconnectTimer = window.setTimeout(connectAlerts, 2000);
      };
    };
    void refresh()
      .then(() => {
        if (!disposed) void refreshAlerts();
      })
      .catch((cause) => {
        setConnection("offline");
        setError(cause instanceof Error ? cause.message : t("qbUnavailable"));
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
    t,
  ]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  // While a called patient is inside the grace period, count down to "Mark absent".
  const graceRunning = items.some(
    (item) => item.status === "called" && graceLeft(item.called_at, graceSeconds, now) > 0,
  );
  useEffect(() => {
    if (!graceRunning) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [graceRunning]);

  async function acknowledge(alertId: string) {
    const response = await staffAuth.request(`/alerts/${alertId}/ack`, {
      method: "POST",
      headers: { "Idempotency-Key": newRequestId() },
    });
    if (!response.ok) {
      setError(t("qbAckFailed"));
      return;
    }
    setAlerts((current) => current.filter((alert) => alert.id !== alertId));
  }

  async function action(visitId: string, name: QueueAction) {
    setWorking(visitId);
    setActionError("");
    setNotice("");
    try {
      const response = await staffAuth.request(`/queue/${visitId}/${name}`, {
        method: "POST",
        headers: { "Idempotency-Key": newRequestId() },
      });
      if (!response.ok)
        throw new Error(
          (await queueProblem(response, t)) ?? t("qbActionUnavailable"),
        );
      if (name === "start") {
        // A started consultation is the case sheet: open it, one click instead of two.
        navigate(`/staff/consultations?visit_id=${visitId}&status=in_consult`);
        return;
      }
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("qbActionFailed"));
    } finally {
      setWorking(undefined);
    }
    // Refused or not, the board shows the queue as it is now.
    await refresh().catch(() => setConnection("offline"));
  }

  async function callNext() {
    if (!departmentId) return;
    setWorking("call-next");
    setActionError("");
    setNotice("");
    try {
      const response = await staffAuth.request(
        `/queue/call-next?department_id=${encodeURIComponent(departmentId)}`,
        { method: "POST", headers: { "Idempotency-Key": newRequestId() } },
      );
      if (!response.ok)
        throw new Error(
          (await queueProblem(response, t)) ?? t("qbCallNextFailed"),
        );
      const body = (await response.json()) as { token?: string };
      if (body.token) setNotice(t("calledToken", { token: body.token }));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("qbCallNextFailed"));
    } finally {
      setWorking(undefined);
    }
    await refresh().catch(() => setConnection("offline"));
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
          "Idempotency-Key": newRequestId(),
        },
        body: JSON.stringify({
          urgency: value.urgency,
          reason: value.reason.trim(),
        }),
      });
      if (!response.ok) throw new Error(t("qbOverrideFailed"));
      setOverrideValues((current) => ({
        ...current,
        [visitId]: { ...value, reason: "" },
      }));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("qbOverrideFailed"));
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
            "Idempotency-Key": newRequestId(),
          },
          body: JSON.stringify({ reason }),
        },
      );
      if (!response.ok) throw new Error(t("qbRouteFailed"));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("qbRouteFailed"));
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
      setError(cause instanceof Error ? cause.message : t("qbLoadMoreFailed"));
    } finally {
      setLoadingMore(false);
    }
  }

  const canOverride =
    staffAuth.user?.role === "nurse" || staffAuth.user?.role === "admin";
  const canOpenCase =
    staffAuth.user?.role === "doctor" || staffAuth.user?.role === "admin";
  // Starting and completing a consultation is for doctors (and administrators).
  const canConsult = canOpenCase;
  const [search, setSearch] = useState("");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const sortedItems = useMemo(() => sortQueue(items), [items]);
  // Search and "priority / urgent only" narrow what is shown; the counts and the next
  // patient to call always come from the whole loaded queue.
  const orderedItems = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return sortedItems.filter(
      (item) =>
        (!attentionOnly || item.urgency !== "routine") &&
        (!needle ||
          item.token.toLowerCase().includes(needle) ||
          (item.complaint ?? "").toLowerCase().includes(needle)),
    );
  }, [sortedItems, search, attentionOnly]);
  const filtersActive = Boolean(search.trim()) || attentionOnly || statusFilter !== "all";
  const nextPatient =
    statusFilter === "all" || statusFilter === "queued"
      ? sortedItems.find((item) => item.status === "queued")
      : undefined;
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
  const kpis = [
    { key: "queued", label: t("queueStatus_queued"), value: counts.queued, icon: Users, tone: "wait" },
    { key: "called", label: t("queueStatus_called"), value: counts.called, icon: PhoneCall, tone: "called" },
    {
      key: "in_consult",
      label: t("queueStatus_in_consult"),
      value: counts.in_consult,
      icon: Stethoscope,
      tone: "consult",
    },
  ] as const;

  function primaryAction(item: QueueItem) {
    if (item.status === "queued")
      return { name: "call" as const, label: t("callPatient"), Icon: PhoneCall, wait: 0 };
    if (item.status === "called") {
      if (canConsult)
        return { name: "start" as const, label: t("qbStartConsult"), Icon: Stethoscope, wait: 0 };
      // At the desk, the next step for a called patient who has not come is a missed call.
      const wait = graceLeft(item.called_at, graceSeconds, now);
      return {
        name: "skip" as const,
        label: wait > 0 ? t("markAbsentIn", { time: formatCountdown(wait) }) : t("markAbsent"),
        Icon: UserX,
        wait,
      };
    }
    if (!canConsult) return null;
    return { name: "complete" as const, label: t("qbComplete"), Icon: CheckCircle2, wait: 0 };
  }

  // Built once per language: the board re-renders on every live update of a long queue.
  const missedTime = useMemo(
    () =>
      new Intl.DateTimeFormat(i18n.language, {
        hour: "numeric",
        minute: "2-digit",
        timeZone: "Asia/Kolkata",
      }),
    [i18n.language],
  );

  function clearFilters() {
    setSearch("");
    setAttentionOnly(false);
    setStatusFilter("all");
  }

  return (
    <section className="staff-queue qb" aria-label={t("qbLiveQueue")}>
      <header className="qb-head">
        <div className="qb-head-title">
          <h2>
            {t("queue")}
            {activeDepartment && <span className="qb-dept-name"> · {activeDepartment.name}</span>}
          </h2>
          <p>{t("qbSubtitle")}</p>
        </div>
        <div className="qb-head-actions">
          <ConnectionIndicator state={connection} />
          <label className="qb-sound" title={t("qbSoundHint")}>
            <input
              type="checkbox"
              checked={soundEnabled}
              onChange={(event) => setSoundEnabled(event.target.checked)}
            />
            {soundEnabled ? <Bell aria-hidden="true" size={16} /> : <BellOff aria-hidden="true" size={16} />}
            {t("qbSound")}
          </label>
          <button type="button" className="qb-btn" onClick={() => void refresh()}>
            <RefreshCw aria-hidden="true" size={16} /> {t("refreshPatients")}
          </button>
        </div>
      </header>

      <div className="qb-stats" role="group" aria-label={t("qbSummary")}>
        {kpis.map(({ key, label, value, icon: Icon, tone }) => (
          <button
            type="button"
            key={key}
            className={`qb-stat qb-stat--${tone}`}
            aria-pressed={statusFilter === key}
            title={t("qbShowOnly", { status: label })}
            onClick={() => setStatusFilter(statusFilter === key ? "all" : key)}
          >
            <span className="qb-stat-icon" aria-hidden="true">
              <Icon size={20} />
            </span>
            <span className="qb-stat-text">
              <strong>{value}</strong>
              <span>{label}</span>
            </span>
          </button>
        ))}
        <button
          type="button"
          className="qb-stat qb-stat--attention"
          aria-pressed={attentionOnly}
          title={t("qbShowAttention")}
          onClick={() => setAttentionOnly(!attentionOnly)}
        >
          <span className="qb-stat-icon" aria-hidden="true">
            <AlertTriangle size={20} />
          </span>
          <span className="qb-stat-text">
            <strong>{counts.attention}</strong>
            <span>{t("qbAttention")}</span>
          </span>
        </button>
      </div>

      <div className="qb-filters" role="search" aria-label={t("qbFilters")}>
        <label className="qb-field">
          <span>{t("department")}</span>
          <span className="qb-control">
            <Stethoscope aria-hidden="true" size={16} />
            <select
              aria-label={t("department")}
              value={departmentId}
              disabled={departments.length === 0}
              onChange={(event) => {
                sequenceRef.current = 0;
                setDepartmentId(event.target.value);
              }}
            >
              {departments.length === 0 && <option value="">{t("qbLoading")}</option>}
              {departments.map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </select>
          </span>
        </label>
        <label className="qb-field">
          <span>{t("qbStatusFilter")}</span>
          <span className="qb-control">
            <select
              aria-label={t("visitStatus")}
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as QueueStatus)}
            >
              <option value="all">{t("qbAllActive")}</option>
              <option value="queued">{t("queueStatus_queued")}</option>
              <option value="called">{t("queueStatus_called")}</option>
              <option value="in_consult">{t("queueStatus_in_consult")}</option>
            </select>
          </span>
        </label>
        <label className="qb-field qb-field--search">
          <span>{t("qbSearch")}</span>
          <span className="qb-control">
            <Search aria-hidden="true" size={16} />
            <input
              type="search"
              value={search}
              placeholder={t("qbSearchPlaceholder")}
              aria-label={t("qbSearchLabel")}
              onChange={(event) => setSearch(event.target.value)}
            />
          </span>
        </label>
        <label className="qb-check">
          <input
            type="checkbox"
            checked={attentionOnly}
            onChange={(event) => setAttentionOnly(event.target.checked)}
          />
          {t("qbAttentionOnly")}
        </label>
      </div>

      {alerts.length > 1 && (
        <p className="qb-alerts-title">{t("qbAlertsCount", { count: alerts.length })}</p>
      )}
      {alerts.length > 0 && (
        <section className="qb-alerts" aria-label={t("qbAlertsLabel")} aria-live="assertive">
          {alerts.map((alert) => {
            const help = alert.rule_id === "PATIENT_HELP";
            // A danger-sign tile is stored by its code; say what the patient tapped.
            const tile = alert.evidence ? DANGER_TILE_LABELS[alert.evidence] : undefined;
            return (
              <div
                className={`qb-alert ${help ? "qb-alert--help" : alert.level === "priority" ? "qb-alert--priority" : "qb-alert--urgent"}`}
                role="alert"
                key={alert.id}
              >
                {help ? <Hand aria-hidden="true" size={20} /> : <AlertTriangle aria-hidden="true" size={20} />}
                <div className="qb-alert-text">
                  <strong>
                    {help ? t("helpRequested") : alert.level === "priority" ? t("priorityAlert") : t("urgentAlert")}
                    {" · "}
                    {alert.token_label ?? t("qbAlertAtKiosk")}
                  </strong>
                  <span>
                    {alert.reason_en ?? alert.rule_id ?? t("qbAlertReview")}
                    {alert.evidence && !help
                      ? alert.rule_id === "AI_TRIAGE_REVIEW" || alert.source === "llm"
                        ? t("qbAlertAiQuoted", { text: alert.evidence })
                        : tile
                          ? t("qbAlertTapped", { text: tile })
                          : t("qbAlertSaid", { text: alert.evidence })
                      : ""}
                  </span>
                </div>
                <button type="button" className="qb-btn" onClick={() => void acknowledge(alert.id)}>
                  {t("qbAcknowledge")}
                </button>
              </div>
            );
          })}
        </section>
      )}

      {error && <p className="qb-error" role="alert">{error}</p>}
      {actionError && <p className="qb-error" role="alert">{actionError}</p>}
      {notice && <p className="qb-notice" role="status">{notice}</p>}

      {missed.length > 0 && (
        <section className="qb-missed" aria-label={t("missedTitle")}>
          <header className="qb-missed-head">
            <h3>
              <UserX aria-hidden="true" size={18} /> {t("missedTitle")}
              <span className="qb-count">{missed.length}</span>
            </h3>
            <p>{t("missedLead")}</p>
          </header>
          <ul className="qb-missed-list">
            {missed.map((patient) => (
              <li key={patient.visit_id} className={`qb-missed-item qb-missed-item--${patient.urgency}`}>
                <strong className="qb-missed-token">{patient.token}</strong>
                <span className="qb-missed-text">
                  <span>
                    {ageLabel(patient.age_years, t)} · {sexLabel(patient.sex, t)}
                    {patient.complaint ? ` · ${sentenceCase(patient.complaint)}` : ""}
                  </span>
                  <small>
                    {patient.skipped_at
                      ? t("missedAt", { time: missedTime.format(new Date(patient.skipped_at)) })
                      : null}
                    {" · "}
                    {t("missedCount", { count: patient.skip_count })}
                  </small>
                </span>
                <UrgencyBadge level={patient.urgency} />
                <span className="qb-missed-actions">
                  <button
                    type="button"
                    className="qb-btn qb-btn--primary"
                    disabled={working === patient.visit_id}
                    title={t("recallHint")}
                    onClick={() => void action(patient.visit_id, "recall")}
                  >
                    <RotateCcw aria-hidden="true" size={16} /> {t("recall")}
                  </button>
                  <Popover
                    align="end"
                    triggerClassName="qb-more"
                    panelClassName="qb-menu"
                    ariaLabel={t("qbMoreActionsFor", { token: patient.token })}
                    label={<MoreHorizontal aria-hidden="true" size={18} />}
                  >
                    {(closeMenu) => (
                      <button
                        type="button"
                        className="qb-btn"
                        disabled={working === patient.visit_id}
                        onClick={() => {
                          closeMenu();
                          void action(patient.visit_id, "no-show");
                        }}
                      >
                        {t("markNoShow")}
                      </button>
                    )}
                  </Popover>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="qb-list" aria-label={t("qbPatientsInQueue")}>
        <header className="qb-list-head">
          <h3>
            <Users aria-hidden="true" size={18} /> {t("qbPatientsInQueue")}
            <span className="qb-count">
              {orderedItems.length === items.length
                ? t("qbPatientCount", { count: items.length })
                : t("qbShownOf", { shown: orderedItems.length, total: items.length })}
            </span>
          </h3>
          <span className="qb-sort">
            <ListOrdered aria-hidden="true" size={16} /> {t("qbSorted")}
          </span>
        </header>

        {!queueLoaded && !error && <p className="qb-state" role="status">{t("loadingQueue")}</p>}
        {queueLoaded && departments.length === 0 && !error && (
          <p className="qb-state" role="status">{t("noQueueDepartments")}</p>
        )}
        {queueLoaded && departments.length > 0 && orderedItems.length === 0 && (
          <div className="qb-empty" role="status">
            <Users aria-hidden="true" size={28} />
            <p>{items.length > 0 ? t("qbNoFilterMatches") : t("noQueueMatches")}</p>
            {filtersActive && (
              <button type="button" className="qb-btn" onClick={clearFilters}>
                {t("clearFilters")}
              </button>
            )}
          </div>
        )}

        {orderedItems.length > 0 && (
          <div className="qb-table">
            <div className="qb-row qb-row--head" aria-hidden="true">
              <span>{t("visitToken")}</span>
              <span>{t("patientColumn")}</span>
              <span>{t("qbColComplaint")}</span>
              <span>{t("qbColPriority")}</span>
              <span>{t("qbColWaiting")}</span>
              <span>{t("visitStatus")}</span>
              <span className="qb-col-actions">{t("qbColActions")}</span>
            </div>
            <div className="staff-queue-viewport qb-viewport" ref={scrollRef} role="region" aria-label={t("qbViewport")}>
              <div className="staff-queue-virtual" style={{ height: `${virtualizer.getTotalSize()}px` }}>
                {virtualizer.getVirtualItems().map((virtualRow) => {
                  const item = orderedItems[virtualRow.index];
                  const busy = working === item.visit_id || Boolean(error);
                  const next = primaryAction(item);
                  const absentWait =
                    item.status === "called" ? graceLeft(item.called_at, graceSeconds, now) : 0;
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
                            <span>{t("qbPosition", { position: item.position })}</span>
                          </div>
                          <div className="qb-patient">
                            <UserRound aria-hidden="true" size={16} />
                            <span>
                              {ageLabel(item.age_years, t)} · {sexLabel(item.sex, t)}
                            </span>
                          </div>
                          <div className="qb-complaint" title={item.complaint}>
                            <span className="qb-complaint-text">
                              {sentenceCase(item.complaint) || <span className="qb-muted">{t("qbNotRecorded")}</span>}
                            </span>
                            {item.needs_assistance && (
                              <span className="qb-flag">
                                <Hand aria-hidden="true" size={13} /> {t("patientNeedsAssistance")}
                              </span>
                            )}
                            {item.channel === "phone" && (
                              <span className="qb-flag qb-flag--phone" title={t("channelPhoneHint")}>
                                <Smartphone aria-hidden="true" size={13} /> {t("channelPhone")}
                              </span>
                            )}
                            {item.nurse_check && (
                              <span className="qb-flag qb-flag--check" title={t("nurseCheck")}>
                                <ShieldAlert aria-hidden="true" size={13} /> {t("nurseCheckShort")}
                              </span>
                            )}
                            {(item.skip_count ?? 0) > 0 && (
                              <span className="qb-flag qb-flag--missed">
                                <RotateCcw aria-hidden="true" size={13} />{" "}
                                {t("missedCount", { count: item.skip_count })}
                              </span>
                            )}
                          </div>
                          <div className="qb-urgency">
                            <UrgencyBadge level={item.urgency} />
                            {item.triage && <TriageWhy triage={item.triage} queuedAt={item.queued_at} />}
                          </div>
                          <div className="qb-wait">
                            <Clock3 aria-hidden="true" size={15} />
                            {formatWait(waitMinutes(item.queued_at, now), t)}
                          </div>
                          <div className="qb-status-cell">
                            <span className={`qb-status qb-status--${item.status}`}>{statusLabel(item.status, t)}</span>
                            {item.status !== "queued" && (item.called_by_me || item.called_by_name) && (
                              <small className="qb-called-by">
                                {item.called_by_me
                                  ? t("calledByYou")
                                  : t("calledBy", { name: item.called_by_name })}
                              </small>
                            )}
                          </div>
                          <div className="qb-col-actions qb-actions">
                            {canOpenCase && (
                              <Link className="qb-btn" to={caseLink} title={t("openCaseSheet")}>
                                <FileText aria-hidden="true" size={16} />
                                <span className="qb-btn-label">{t("openCaseSheet")}</span>
                              </Link>
                            )}
                            {next && (
                              <button
                                type="button"
                                className="qb-btn qb-btn--primary"
                                disabled={busy || next.wait > 0}
                                title={next.wait > 0 ? t("markAbsentHint") : undefined}
                                onClick={() => void action(item.visit_id, next.name)}
                              >
                                <next.Icon aria-hidden="true" size={16} />
                                {next.label}
                              </button>
                            )}
                            {(item.status === "called" || (item.status === "queued" && canOverride)) && (
                              <Popover
                                align="end"
                                triggerClassName="qb-more"
                                panelClassName="qb-menu"
                                ariaLabel={t("qbMoreActionsFor", { token: item.token })}
                                label={<MoreHorizontal aria-hidden="true" size={18} />}
                              >
                                {(closeMenu) => (
                                  <>
                                    {item.status === "called" && (
                                      <>
                                        {canConsult && (
                                          <button
                                            type="button"
                                            className="qb-btn"
                                            disabled={busy || absentWait > 0}
                                            title={absentWait > 0 ? t("markAbsentHint") : undefined}
                                            onClick={() => {
                                              closeMenu();
                                              void action(item.visit_id, "skip");
                                            }}
                                          >
                                            <UserX aria-hidden="true" size={15} />
                                            {absentWait > 0
                                              ? t("markAbsentIn", { time: formatCountdown(absentWait) })
                                              : t("markAbsent")}
                                          </button>
                                        )}
                                        <button
                                          type="button"
                                          className="qb-btn"
                                          disabled={busy}
                                          onClick={() => {
                                            closeMenu();
                                            void action(item.visit_id, "return");
                                          }}
                                        >
                                          <Undo2 aria-hidden="true" size={15} /> {t("putBack")}
                                        </button>
                                      </>
                                    )}
                                    {canOverride && item.status === "queued" && (
                                      <div className="qb-override">
                                        <strong>{t("qbChangePriority")}</strong>
                                        <label>
                                          {t("qbColPriority")}
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
                                            <option value="routine">{t("qbLevelRoutine")}</option>
                                            <option value="priority">{t("qbLevelPriority")}</option>
                                            <option value="urgent">{t("qbLevelUrgent")}</option>
                                          </select>
                                        </label>
                                        <label>
                                          {t("qbReason")}
                                          <input
                                            value={overrideValues[item.visit_id]?.reason ?? ""}
                                            maxLength={500}
                                            minLength={5}
                                            placeholder={t("qbReasonHint")}
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
                                          {t("qbApplyOverride")}
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
                              {t("qbRouteUnconfirmed")} <b>{routeLabel(item.routing.department_code, t)}</b>
                            </span>
                            {canOverride ? (
                              <span className="qb-route-form">
                                <input
                                  aria-label={t("qbReason")}
                                  placeholder={t("qbReasonPlaceholder")}
                                  value={routeReasons[item.visit_id] ?? ""}
                                  maxLength={500}
                                  minLength={5}
                                  onChange={(event) =>
                                    setRouteReasons((current) => ({ ...current, [item.visit_id]: event.target.value }))
                                  }
                                />
                                <button
                                  type="button"
                                  className="qb-btn"
                                  disabled={busy || (routeReasons[item.visit_id]?.trim().length ?? 0) < 5}
                                  onClick={() => void confirmRoute(item.visit_id)}
                                >
                                  {t("qbConfirmDepartment")}
                                </button>
                              </span>
                            ) : (
                              <span className="qb-muted">{t("qbNurseWillConfirm")}</span>
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
                className="qb-btn staff-queue-load-more"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? t("qbLoading") : t("qbLoadMore")}
              </button>
            )}
          </div>
        )}
      </section>

      {nextPatient && (
        <section className="qb-next" aria-label={t("qbNextAction")}>
          <div className="qb-next-text">
            <span className="qb-next-label">
              <PhoneCall aria-hidden="true" size={16} /> {t("qbNextToCall")}
            </span>
            <strong>{nextPatient.token}</strong>
            <span>
              {ageLabel(nextPatient.age_years, t)} · {sexLabel(nextPatient.sex, t)}
              {nextPatient.complaint ? ` · ${sentenceCase(nextPatient.complaint)}` : ""}
            </span>
          </div>
          <UrgencyBadge level={nextPatient.urgency} />
          {/* The server picks the patient, so two doctors pressing at once get different ones. */}
          <button
            type="button"
            className="qb-btn qb-btn--primary qb-btn--large"
            disabled={working === "call-next" || Boolean(error)}
            onClick={() => void callNext()}
          >
            <PhoneCall aria-hidden="true" size={18} /> {t("callNext")}
          </button>
        </section>
      )}
    </section>
  );
}
