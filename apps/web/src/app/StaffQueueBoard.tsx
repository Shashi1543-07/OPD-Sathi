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
};

const apiOrigin = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function StaffQueueBoard() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [error, setError] = useState("");
  const [working, setWorking] = useState<string>();
  const refresh = useCallback(async () => {
    const response = await staffAuth.request("/queue");
    if (!response.ok) throw new Error("Queue is temporarily unavailable.");
    const body = (await response.json()) as { items: QueueItem[] };
    setItems(body.items);
  }, []);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | undefined;
    let initialTimer: number | undefined;
    const poll = window.setInterval(() => void refresh().catch(() => setError("Reconnecting to the queue…")), 5000);
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
        const message = JSON.parse(String(event.data)) as { type?: string };
        if (message.type === "ping") socket?.send(JSON.stringify({ type: "pong" }));
        if (message.type === "queue.snapshot" || message.type === "queue.delta") void refresh();
      };
      socket.onclose = () => {
        if (!disposed) window.setTimeout(connect, 2000);
      };
    };
    initialTimer = window.setTimeout(() => {
      void refresh().then(connect).catch(() => setError("Queue is temporarily unavailable."));
    }, 0);
    return () => {
      disposed = true;
      window.clearInterval(poll);
      window.clearTimeout(initialTimer);
      socket?.close();
    };
    // The first queue load selects the stream's department.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, items.length ? items[0].department_id : ""]);

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

  return (
    <section className="staff-queue" aria-label="Live patient queue">
      <div className="staff-queue-heading"><div><h2>Queue</h2><p>Live waiting room</p></div><button type="button" className="ui-button ui-button--secondary" onClick={() => void refresh()}>Refresh</button></div>
      {error && <p role="alert">{error}</p>}
      {items.length === 0 ? <p role="status">No patients are waiting.</p> : (
        <div className="staff-queue-list">
          {items.map((item) => (
            <article className="staff-queue-item" key={item.visit_id}>
              <div><strong>{item.token}</strong><span>Position {item.position} · {item.status.replace("_", " ")}</span></div>
              <span className={item.urgency === "urgent" ? "queue-urgency queue-urgency--urgent" : "queue-urgency"}>{item.urgency === "urgent" ? "Needs early attention" : item.urgency}</span>
              <div className="staff-queue-actions">
                {item.status === "queued" && <button type="button" disabled={working === item.visit_id} onClick={() => void action(item.visit_id, "call")}>Call</button>}
                {item.status === "called" && <button type="button" disabled={working === item.visit_id} onClick={() => void action(item.visit_id, "start")}>Start</button>}
                {item.status === "in_consult" && <button type="button" disabled={working === item.visit_id} onClick={() => void action(item.visit_id, "complete")}>Complete</button>}
                {(item.status === "queued" || item.status === "called") && <button type="button" disabled={working === item.visit_id} onClick={() => void action(item.visit_id, "no-show")}>No show</button>}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
