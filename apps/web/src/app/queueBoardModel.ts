export type QueueItem = {
  visit_id: string;
  department_id: string;
  token: string;
  urgency: "routine" | "priority" | "urgent";
  status: "queued" | "called" | "in_consult";
  age_years: number | null;
  sex: string;
  complaint: string;
  position: number;
  queued_at: string | null;
  needs_assistance?: boolean;
  routing?: {
    department_code: string;
    needs_nurse_confirmation: boolean;
  } | null;
};

export type QueueOperation =
  | { op: "remove"; visit_id: string }
  | {
      op: "upsert";
      item: Partial<QueueItem> &
        Pick<QueueItem, "visit_id" | "token" | "urgency" | "status">;
    };
export type QueueStatus = "all" | QueueItem["status"];
type SequenceAction = "ignore" | "apply" | "resync";

export function waitMinutes(
  queuedAt: string | null,
  now: number,
): number | null {
  if (!queuedAt) return null;
  const queuedAtMs = Date.parse(queuedAt);
  if (!Number.isFinite(queuedAtMs)) return null;
  return Math.max(0, Math.floor((now - queuedAtMs) / 60_000));
}

export function sortQueue(items: QueueItem[]): QueueItem[] {
  return [...items].sort((a, b) => {
    const urgentOrder =
      Number(b.urgency === "urgent") - Number(a.urgency === "urgent");
    return (
      urgentOrder || a.position - b.position || a.token.localeCompare(b.token)
    );
  });
}

export function sequenceAction(
  lastSequence: number,
  incomingSequence: number,
): SequenceAction {
  if (incomingSequence <= lastSequence) return "ignore";
  return incomingSequence === lastSequence + 1 ? "apply" : "resync";
}

export function applyQueueOperations(
  current: QueueItem[],
  operations: QueueOperation[],
  statusFilter: QueueStatus,
): { items: QueueItem[]; needsResync: boolean } {
  let rows = current;
  let needsResync = false;
  for (const operation of operations) {
    if (operation.op === "remove") {
      // The Redis waiting set may remove a row that remains active as called or in consult.
      needsResync = true;
      continue;
    }
    const index = rows.findIndex(
      (item) => item.visit_id === operation.item.visit_id,
    );
    if (index === -1) {
      needsResync = true;
      continue;
    }
    if (
      statusFilter !== "all" &&
      operation.item.status &&
      operation.item.status !== statusFilter
    ) {
      rows = rows.filter((item) => item.visit_id !== operation.item.visit_id);
      continue;
    }
    rows = rows.map((item, rowIndex) =>
      rowIndex === index ? { ...item, ...operation.item } : item,
    );
  }
  return { items: rows, needsResync };
}
