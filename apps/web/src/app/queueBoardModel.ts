/** Staff-only explanation of a patient's place: score, every point's reason, AI check. */
export type Triage = {
  score: number;
  level: "routine" | "priority" | "urgent";
  head_start_min: number | null;
  components: { points: number; label: string; kind: string; source?: string }[];
  ai_review?: { verdict: "agree" | "more_urgent" | "less_urgent"; reason_en: string } | null;
  override?: { level: string; reason: string; by_role?: string } | null;
};

export type QueueItem = {
  triage?: Triage | null;
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
  /** Where the patient registered: a kiosk screen, their own phone, or the desk. */
  channel?: "kiosk" | "phone" | "desk";
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
