import { useCallback, useEffect, useState } from "react";
import { staffAuth } from "../lib/auth";

type QueueItem = {
  visit_id: string;
  department_id: string;
  token: string;
  urgency: string;
  status: string;
  age_years: number | null;
  position: number;
  routing?: { department_code: string; needs_nurse_confirmation: boolean } | null;
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

const apiOrigin = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function StaffQueueBoard() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [alerts, setAlerts] = useState<StaffAlert[]>([]);
  const [error, setError] = useState("");
  const [working, setWorking] = useState<string>();
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [overrideValues, setOverrideValues] = useState<Record<string, { urgency: string; reason: string }>>({});
  const [routeReasons, setRouteReasons] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    const response = await staffAuth.request("/queue");
    if (!response.ok) throw new Error("Queue is temporarily unavailable.");
    const body = (await response.json()) as { items: QueueItem[] };
    setItems(body.items);
  }, []);

  const refreshAlerts = useCallback(async () => {
    const response = await staffAuth.request("/alerts?acknowledged=false");
    if (!response.ok) return;
    const body = (await response.json()) as { items: StaffAlert[] };
    setAlerts(body.items);
  }, []);

  function chime() {
    if (!soundEnabled) return;
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
  }

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

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | undefined;
    let alertSocket: WebSocket | undefined;
    let initialTimer: number | undefined;
    const poll = window.setInterval(() => {
      void refresh().catch(() => setError("Reconnecting to the queue…"));
      void refreshAlerts();
    }, 5000);
    const connect = () => {
      if (disposed) return;
      const token = staffAuth.accessToken;
      const department = items[0]?.department_id;
      if (!token || !department) return;
      const api = new URL(apiOrigin);
      api.protocol = api.protocol === "https:" ? "wss:" : "ws:";
      api.pathname = "/ws/staff/queue";
      api.searchParams.set("department_id", department);
      socket = new WebSocket(api);
      socket.onopen = () => socket?.send(JSON.stringify({ type: "auth", token }));
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as StaffAlert & { type?: string };
        if (message.type === "ping") socket?.send(JSON.stringify({ type: "pong" }));
        if (message.type === "queue.snapshot" || message.type === "queue.delta") void refresh();
        if (message.type === "alert.urgent" || message.type === "alert.escalated") {
          chime();
          setAlerts((current) => current.some((alert) => alert.id === message.id) ? current : [message, ...current]);
        }
      };
      socket.onclose = () => {
        if (!disposed) window.setTimeout(connect, 2000);
      };
    };
    const connectAlerts = () => {
      if (disposed) return;
      const token = staffAuth.accessToken;
      if (!token) return;
      const api = new URL(apiOrigin);
      api.protocol = api.protocol === "https:" ? "wss:" : "ws:";
      api.pathname = "/ws/staff/queue";
      api.searchParams.set("alerts_only", "true");
      alertSocket = new WebSocket(api);
      alertSocket.onopen = () => alertSocket?.send(JSON.stringify({ type: "auth", token }));
      alertSocket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as StaffAlert & { type?: string };
        if (message.type === "ping") alertSocket?.send(JSON.stringify({ type: "pong" }));
        if (message.type === "alert.urgent" || message.type === "alert.escalated") {
          chime();
          setAlerts((current) => current.some((alert) => alert.id === message.id) ? current : [message, ...current]);
        }
      };
      alertSocket.onclose = () => {
        if (!disposed) window.setTimeout(connectAlerts, 2000);
      };
    };
    initialTimer = window.setTimeout(() => {
      void Promise.all([refresh(), refreshAlerts()]).then(() => { connect(); connectAlerts(); }).catch(() => setError("Queue is temporarily unavailable."));
    }, 0);
    return () => {
      disposed = true;
      window.clearInterval(poll);
      window.clearTimeout(initialTimer);
      socket?.close();
      alertSocket?.close();
    };
    // The first queue load selects the stream's department.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, refreshAlerts, soundEnabled, items.length ? items[0].department_id : ""]);

  async function action(visitId: string, name: "call" | "start" | "complete" | "no-show") {
    setWorking(visitId);
    setError("");
    try {
      const response = await staffAuth.request(`/queue/${visitId}/${name}`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      if (!response.ok) throw new Error("That queue action is no longer available.");
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
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ urgency: value.urgency, reason: value.reason.trim() }),
      });
      if (!response.ok) throw new Error("Could not update the queue priority.");
      setOverrideValues((current) => ({ ...current, [visitId]: { ...value, reason: "" } }));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Queue override failed.");
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
      const response = await staffAuth.request(`/queue/${visitId}/routing/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ reason }),
      });
      if (!response.ok) throw new Error("Could not confirm this route.");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Route confirmation failed.");
    } finally {
      setWorking(undefined);
    }
  }

  const canOverride = staffAuth.user?.role === "nurse" || staffAuth.user?.role === "admin";

  return (
    <section className="staff-queue" aria-label="Live patient queue">
      <div className="staff-queue-heading"><div><h2>Queue</h2><p>Live waiting room</p></div><button type="button" className="ui-button ui-button--secondary" onClick={() => void refresh()}>Refresh</button></div>
      <div className="staff-alert-controls"><button type="button" aria-pressed={soundEnabled} onClick={() => setSoundEnabled((enabled) => !enabled)}>{soundEnabled ? "Disable alert sound" : "Enable alert sound"}</button></div>
      {alerts.length > 0 && <section className="staff-alerts" aria-label="Unacknowledged staff alerts" aria-live="assertive">
        {alerts.map((alert) => <article className="staff-alert" role="alert" key={alert.id}>
          <div><strong>Urgent staff alert · {alert.token_label ?? "Visit"}</strong><p>{alert.reason_en ?? alert.rule_id ?? "Review this patient now."}</p>{alert.evidence && <small>Reported: {alert.evidence}</small>}</div>
          <button type="button" onClick={() => void acknowledge(alert.id)}>Acknowledge</button>
        </article>)}
      </section>}
      {error && <p role="alert">{error}</p>}
      {items.length === 0 ? <p role="status">No patients are waiting.</p> : (
        <div className="staff-queue-list">
          {items.map((item) => (
            <article className="staff-queue-item" key={item.visit_id}>
              <div><strong>{item.token}</strong><span>Position {item.position} · {item.status.replace("_", " ")}</span></div>
              <span className={item.urgency === "urgent" ? "queue-urgency queue-urgency--urgent" : "queue-urgency"}>{item.urgency === "urgent" ? "Needs early attention" : item.urgency}</span>
              {item.routing?.needs_nurse_confirmation && <div className="staff-route-confirm">
                <strong>Confirm suggested department: {item.routing.department_code}</strong>
                {canOverride ? <><label>Reason <input value={routeReasons[item.visit_id] ?? ""} maxLength={500} minLength={5} onChange={(event) => setRouteReasons((current) => ({ ...current, [item.visit_id]: event.target.value }))} /></label>
                  <button type="button" disabled={working === item.visit_id || (routeReasons[item.visit_id]?.trim().length ?? 0) < 5} onClick={() => void confirmRoute(item.visit_id)}>Confirm department</button></> : <span>Ask a nurse to confirm this suggested route.</span>}
              </div>}
              <div className="staff-queue-actions">
                {item.status === "queued" && <button type="button" disabled={working === item.visit_id} onClick={() => void action(item.visit_id, "call")}>Call</button>}
                {item.status === "called" && <button type="button" disabled={working === item.visit_id} onClick={() => void action(item.visit_id, "start")}>Start</button>}
                {item.status === "in_consult" && <button type="button" disabled={working === item.visit_id} onClick={() => void action(item.visit_id, "complete")}>Complete</button>}
                {(item.status === "queued" || item.status === "called") && <button type="button" disabled={working === item.visit_id} onClick={() => void action(item.visit_id, "no-show")}>No show</button>}
                {canOverride && item.status === "queued" && <div className="staff-queue-override">
                  <label>Priority <select value={overrideValues[item.visit_id]?.urgency ?? item.urgency} onChange={(event) => setOverrideValues((current) => ({ ...current, [item.visit_id]: { urgency: event.target.value, reason: current[item.visit_id]?.reason ?? "" } }))}><option value="routine">Routine</option><option value="priority">Priority</option><option value="urgent">Urgent</option></select></label>
                  <label>Reason <input value={overrideValues[item.visit_id]?.reason ?? ""} maxLength={500} minLength={5} onChange={(event) => setOverrideValues((current) => ({ ...current, [item.visit_id]: { urgency: current[item.visit_id]?.urgency ?? item.urgency, reason: event.target.value } }))} /></label>
                  <button type="button" disabled={working === item.visit_id || (overrideValues[item.visit_id]?.reason.trim().length ?? 0) < 5} onClick={() => void override(item.visit_id)}>Apply override</button>
                </div>}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
