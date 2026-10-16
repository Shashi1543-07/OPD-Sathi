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
  called_at?: string | null;
  called_by_name?: string | null;
  called_by_me?: boolean;
  /** Times this patient was called and not there (they were recalled each time). */
  skip_count?: number;
  /** A danger sign the patient tapped that no nurse has confirmed yet. */
  nurse_check?: boolean;
};

/** Called and not there: kept apart from the line until staff recall them. */
export type MissedItem = {
  visit_id: string;
  department_id: string;
  token: string;
  urgency: QueueItem["urgency"];
  age_years: number | null;
  sex: string;
  complaint: string;
  skip_count: number;
  skipped_at: string | null;
  queued_at: string | null;
};

/** Seconds left before a called patient may be marked absent (0 once allowed). */
export function graceLeft(
  calledAt: string | null | undefined,
  graceSeconds: number,
  now: number,
): number {
  if (!calledAt) return 0;
  const calledAtMs = Date.parse(calledAt);
  if (!Number.isFinite(calledAtMs)) return 0;
  return Math.max(0, Math.ceil((calledAtMs + graceSeconds * 1000 - now) / 1000));
}

/** "2:05" for a short countdown. */
export function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

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
