import { useCallback, useEffect, useState } from "react";
import { staffAuth } from "../lib/auth";
import { sortQueue, type QueueItem } from "./queueBoardModel";

export type ConsultQueue = {
  items: QueueItem[];
  state: "loading" | "ready" | "error";
  now: number;
  reload: () => void;
};

/** Today's active patients for the consultation page, refreshed every 30 s and on patient change. */
export function useConsultQueue(activeVisitId: string | null): ConsultQueue {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [state, setState] = useState<ConsultQueue["state"]>("loading");
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const response = await staffAuth.request("/queue?limit=50", { cache: "no-store" });
      if (!response.ok) throw new Error("queue unavailable");
      const body = (await response.json()) as { items?: QueueItem[] };
      setItems(sortQueue(body.items ?? []));
      setNow(Date.now());
      setState("ready");
    } catch {
      setState("error");
    }
  }, []);

  useEffect(() => {
    const first = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(), 30_000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [load, activeVisitId]);

  const reload = useCallback(() => void load(), [load]);
  return { items, state, now, reload };
}
