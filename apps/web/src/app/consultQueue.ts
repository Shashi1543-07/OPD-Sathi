import { useCallback, useEffect, useState } from "react";
import { staffAuth } from "../lib/auth";
import { sortQueue, type MissedItem, type QueueItem } from "./queueBoardModel";

export type ConsultQueue = {
  items: QueueItem[];
  /** Called and not there, waiting for a recall. */
  missed?: MissedItem[];
  /** Seconds after a call before a patient may be marked absent. */
  graceSeconds?: number;
  state: "loading" | "ready" | "error";
  now: number;
  reload: () => void;
};

/** Today's active patients for the consultation page, refreshed every 30 s and on patient change. */
export function useConsultQueue(activeVisitId: string | null): ConsultQueue {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [missed, setMissed] = useState<MissedItem[]>([]);
  const [graceSeconds, setGraceSeconds] = useState<number>();
  const [state, setState] = useState<ConsultQueue["state"]>("loading");
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const response = await staffAuth.request("/queue?limit=50", { cache: "no-store" });
      if (!response.ok) throw new Error("queue unavailable");
      const body = (await response.json()) as {
        items?: QueueItem[];
        missed?: MissedItem[];
        call_grace_seconds?: number;
      };
      setItems(sortQueue(body.items ?? []));
      setMissed(body.missed ?? []);
      setGraceSeconds(body.call_grace_seconds);
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
  return { items, missed, graceSeconds, state, now, reload };
}
