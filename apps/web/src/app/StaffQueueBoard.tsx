import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ConnectionIndicator, UrgencyBadge } from "../components/ui";
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
};
type ConnectionState = "live" | "reconnecting" | "offline";

const apiOrigin = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const PAGE_SIZE = 50;
const SOUND_PREFERENCE_KEY = "opd-staff-alert-sound";

function initialSoundPreference(): boolean {
  try {
    return window.localStorage.getItem(SOUND_PREFERENCE_KEY) === "enabled";
  } catch {
    return false;
  }
}

export function StaffQueueBoard() {
  const { t } = useTranslation("staff");
  const [items, setItems] = useState<QueueItem[]>([]);
  const [departments, setDepartments] = useState<QueueDepartment[]>([]);
  const [departmentId, setDepartmentId] = useState("");
  const [statusFilter, setStatusFilter] = useState<QueueStatus>("all");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
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
      const response = await staffAuth.request(`/queue?${query.toString()}`);
      if (!response.ok) throw new Error("Queue is temporarily unavailable.");
      const body = (await response.json()) as {
        items: QueueItem[];
        next_cursor: string | null;
        departments?: QueueDepartment[];
      };
      if (requestScopeRef.current !== requestScope) return;
      if (body.departments) {
        setDepartments(body.departments);
        if (!departmentId && body.departments[0])
          setDepartmentId(body.departments[0].id);
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
      setError("");
    },
    [departmentId, statusFilter],
  );

  const refreshAlerts = useCallback(async () => {
    const query = new URLSearchParams({ acknowledged: "false" });
    if (departmentId) query.set("department_id", departmentId);
    const response = await staffAuth.request(`/alerts?${query.toString()}`);
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
    let disposed = false;
    let socket: WebSocket | undefined;
    let alertSocket: WebSocket | undefined;
    let reconnectTimer: number | undefined;
    let alertReconnectTimer: number | undefined;
    const poll = window.setInterval(() => {
      void refresh().catch(() => {
        setConnection("offline");
        setError("Queue is temporarily unavailable. Reconnecting…");
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
      const api = new URL(apiOrigin);
      api.protocol = api.protocol === "https:" ? "wss:" : "ws:";
      api.pathname = "/ws/staff/queue";
      api.search = "";
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
      const api = new URL(apiOrigin);
      api.protocol = api.protocol === "https:" ? "wss:" : "ws:";
      api.pathname = "/ws/staff/queue";
      api.search = "";
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
      .catch(() => {
        setConnection("offline");
        setError("Queue is temporarily unavailable.");
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
  }, [departmentId, statusFilter, refresh, refreshAlerts, chime]);

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
  const canConsult = staffAuth.user?.role === "doctor";
  const orderedItems = useMemo(() => sortQueue(items), [items]);
  // The virtualizer exposes imperative measurements; React Compiler intentionally skips it.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: orderedItems.length,
    getScrollElement: () => scrollElement,
    estimateSize: () => 196,
    overscan: 8,
  });

  return (
    <section className="staff-queue" aria-label="Live patient queue">
      <div className="staff-queue-heading">
        <div>
          <h2>Queue</h2>
          <p>Live waiting room</p>
        </div>
        <div className="staff-queue-heading-actions">
          <ConnectionIndicator state={connection} />
          <button
            type="button"
            className="ui-button ui-button--secondary"
            onClick={() => void refresh()}
          >
            Refresh
          </button>
        </div>
      </div>
      <div className="staff-queue-filters">
        <div
          className="staff-department-tabs"
          role="group"
          aria-label="Department"
        >
          {departments.map((department) => (
            <button
              type="button"
              key={department.id}
              aria-pressed={department.id === departmentId}
              onClick={() => {
                if (department.id === departmentId) return;
                setItems([]);
                loadedIdsRef.current.clear();
                setNextCursor(null);
                sequenceRef.current = 0;
                setDepartmentId(department.id);
              }}
            >
              {department.name}
            </button>
          ))}
        </div>
        <label>
          Status
          <select
            aria-label="Status"
            value={statusFilter}
            onChange={(event) => {
              setItems([]);
              loadedIdsRef.current.clear();
              setNextCursor(null);
              setStatusFilter(event.target.value as QueueStatus);
            }}
          >
            <option value="all">All active</option>
            <option value="queued">Waiting</option>
            <option value="called">Called</option>
            <option value="in_consult">In consultation</option>
          </select>
        </label>
        <label className="staff-alert-sound">
          <input
            type="checkbox"
            checked={soundEnabled}
            onChange={(event) => setSoundEnabled(event.target.checked)}
          />{" "}
          Alert sound
        </label>
      </div>
      {alerts.length > 0 && (
        <section
          className="staff-alerts"
          aria-label="Unacknowledged staff alerts"
          aria-live="assertive"
        >
          {alerts.map((alert) => (
            <article className="staff-alert" role="alert" key={alert.id}>
              <div>
                <strong>
                  Urgent staff alert · {alert.token_label ?? "Visit"}
                </strong>
                <p>
                  {alert.reason_en ??
                    alert.rule_id ??
                    "Review this patient now."}
                </p>
                {alert.evidence && <small>Reported: {alert.evidence}</small>}
              </div>
              <button type="button" onClick={() => void acknowledge(alert.id)}>
                Acknowledge
              </button>
            </article>
          ))}
        </section>
      )}
      {error && <p role="alert">{error}</p>}
      {departments.length === 0 && !error && (
        <p role="status">Loading departments…</p>
      )}
      {departments.length > 0 && items.length === 0 && (
        <p role="status">No patients match these queue filters.</p>
      )}
      {items.length > 0 && (
        <>
          <div className="staff-queue-columns" aria-hidden="true">
            <span>Token</span>
            <span>Patient</span>
            <span>Complaint</span>
            <span>Urgency / wait</span>
            <span>Status</span>
          </div>
          <div
            className="staff-queue-viewport"
            ref={scrollRef}
            role="region"
            aria-label="Queue patients"
          >
            <div
              className="staff-queue-virtual"
              style={{ height: `${virtualizer.getTotalSize()}px` }}
            >
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const item = orderedItems[virtualRow.index];
                const waited = waitMinutes(item.queued_at, now);
                return (
                  <article
                    className={`staff-queue-item${item.urgency === "urgent" ? " staff-queue-item--urgent" : ""}`}
                    key={item.visit_id}
                    data-index={virtualRow.index}
                    ref={virtualizer.measureElement}
                    style={{ transform: `translateY(${virtualRow.start}px)` }}
                  >
                    <div className="staff-queue-cell staff-queue-token">
                      <strong>{item.token}</strong>
                      <span>Position {item.position}</span>
                    </div>
                    <div className="staff-queue-cell">
                      <span>
                        {item.age_years === null
                          ? "Age not recorded"
                          : `${item.age_years} years`}
                      </span>
                      <span>
                        {item.sex === "unknown" ? "Sex not recorded" : item.sex}
                      </span>
                    </div>
                    <div className="staff-queue-cell staff-queue-complaint">
                      {item.complaint || "Complaint not recorded"}
                    </div>
                    <div className="staff-queue-cell">
                      <UrgencyBadge level={item.urgency} />
                      <span>
                        {waited === null
                          ? "Wait time unavailable"
                          : `${waited} min waiting`}
                      </span>
                    </div>
                    <div className="staff-queue-cell staff-queue-status">
                      {item.status.replaceAll("_", " ")}
                    </div>
                    {item.routing?.needs_nurse_confirmation && (
                      <div className="staff-route-confirm">
                        <strong>
                          Confirm suggested department:{" "}
                          {item.routing.department_code}
                        </strong>
                        {canOverride ? (
                          <>
                            <label>
                              Reason{" "}
                              <input
                                value={routeReasons[item.visit_id] ?? ""}
                                maxLength={500}
                                minLength={5}
                                onChange={(event) =>
                                  setRouteReasons((current) => ({
                                    ...current,
                                    [item.visit_id]: event.target.value,
                                  }))
                                }
                              />
                            </label>
                            <button
                              type="button"
                              disabled={
                                working === item.visit_id ||
                                (routeReasons[item.visit_id]?.trim().length ??
                                  0) < 5
                              }
                              onClick={() => void confirmRoute(item.visit_id)}
                            >
                              Confirm department
                            </button>
                          </>
                        ) : (
                          <span>
                            Ask a nurse to confirm this suggested route.
                          </span>
                        )}
                      </div>
                    )}
                    <div className="staff-queue-actions">
                      {canConsult && (
                        <Link
                          to={`/staff/consultations?visit_id=${item.visit_id}&status=${item.status}`}
                        >
                          {t("openCaseSheet")}
                        </Link>
                      )}
                      {item.status === "queued" && (
                        <button
                          type="button"
                          disabled={working === item.visit_id}
                          onClick={() => void action(item.visit_id, "call")}
                        >
                          Call
                        </button>
                      )}
                      {item.status === "called" && (
                        <button
                          type="button"
                          disabled={working === item.visit_id}
                          onClick={() => void action(item.visit_id, "start")}
                        >
                          Start
                        </button>
                      )}
                      {item.status === "in_consult" && (
                        <button
                          type="button"
                          disabled={working === item.visit_id}
                          onClick={() => void action(item.visit_id, "complete")}
                        >
                          Complete
                        </button>
                      )}
                      {(item.status === "queued" ||
                        item.status === "called") && (
                        <button
                          type="button"
                          disabled={working === item.visit_id}
                          onClick={() => void action(item.visit_id, "no-show")}
                        >
                          No show
                        </button>
                      )}
                      {canOverride && item.status === "queued" && (
                        <div className="staff-queue-override">
                          <label>
                            Priority{" "}
                            <select
                              value={
                                overrideValues[item.visit_id]?.urgency ??
                                item.urgency
                              }
                              onChange={(event) =>
                                setOverrideValues((current) => ({
                                  ...current,
                                  [item.visit_id]: {
                                    urgency: event.target.value,
                                    reason:
                                      current[item.visit_id]?.reason ?? "",
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
                            Reason{" "}
                            <input
                              value={
                                overrideValues[item.visit_id]?.reason ?? ""
                              }
                              maxLength={500}
                              minLength={5}
                              onChange={(event) =>
                                setOverrideValues((current) => ({
                                  ...current,
                                  [item.visit_id]: {
                                    urgency:
                                      current[item.visit_id]?.urgency ??
                                      item.urgency,
                                    reason: event.target.value,
                                  },
                                }))
                              }
                            />
                          </label>
                          <button
                            type="button"
                            disabled={
                              working === item.visit_id ||
                              (overrideValues[item.visit_id]?.reason.trim()
                                .length ?? 0) < 5
                            }
                            onClick={() => void override(item.visit_id)}
                          >
                            Apply override
                          </button>
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
              className="ui-button ui-button--secondary staff-queue-load-more"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {loadingMore ? "Loading…" : "Load more patients"}
            </button>
          )}
        </>
      )}
    </section>
  );
}
