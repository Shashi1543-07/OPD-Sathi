import { Bot, UserCheck } from "lucide-react";
import { Popover } from "../components/ui";
import { withTileLabels } from "../lib/dangerTiles";
import type { Triage } from "./queueBoardModel";

const KIND_LABEL: Record<string, string> = {
  red_flag: "warning sign",
  signal: "answer",
  vulnerability: "patient group",
  onset: "onset",
  self_rating: "own rating",
};
const AI_VERDICT: Record<string, string> = {
  agree: "AI check agrees with this level",
  more_urgent: "AI check: may be MORE urgent",
  less_urgent: "AI check: may be less urgent",
};
const LEVEL: Record<string, string> = { routine: "Routine", priority: "Priority", urgent: "Urgent" };

function clock(date: Date) {
  return date.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
}

/**
 * Why a patient is where they are in the queue. Staff only: patients never see the score.
 * Every point is listed with its reason, so the order can be checked and challenged.
 */
export function TriageWhy({ triage, queuedAt }: { triage: Triage; queuedAt: string | null }) {
  const arrived = queuedAt ? new Date(queuedAt) : null;
  const placed =
    arrived && triage.head_start_min
      ? new Date(arrived.getTime() - triage.head_start_min * 60_000)
      : null;
  return (
    <Popover
      triggerClassName="qb-why"
      panelClassName="qb-why-panel"
      ariaLabel={`Triage score ${triage.score}: why`}
      label={
        <>
          Score {triage.score}
          <span aria-hidden="true"> · </span>
          <span className="qb-why-link">Why?</span>
        </>
      }
    >
        <p className="qb-why-total">
          <strong>
            {triage.score}/100 · {LEVEL[triage.level] ?? triage.level}
          </strong>
          <span>Patients never see this score.</span>
        </p>
        {triage.components.length === 0 ? (
          <p className="qb-muted">No warning signs reported: waits in arrival order.</p>
        ) : (
          <ul>
            {triage.components.map((component, index) => (
              <li key={`${component.kind}-${index}`}>
                <b>+{component.points}</b>
                <span>{withTileLabels(component.label)}</span>
                <em>
                  {KIND_LABEL[component.kind] ?? component.kind}
                  {component.source === "AI second opinion" ? " · AI" : ""}
                </em>
              </li>
            ))}
          </ul>
        )}
        <p className="qb-why-place">
          {triage.level === "urgent"
            ? "Urgent: placed at the front of the queue."
            : arrived && placed && triage.head_start_min
              ? `Arrived ${clock(arrived)}; placed as if ${clock(placed)} (${triage.head_start_min}-minute head start, never more than 45).`
              : "No head start: waits in arrival order."}
        </p>
        {triage.override && (
          <p className="qb-why-override">
            <UserCheck aria-hidden="true" size={15} />
            Set to {LEVEL[triage.override.level] ?? triage.override.level} by{" "}
            {triage.override.by_role ?? "staff"}: “{triage.override.reason}”
          </p>
        )}
        <p className={`qb-why-ai qb-why-ai--${triage.ai_review?.verdict ?? "none"}`}>
          <Bot aria-hidden="true" size={15} />
          {triage.ai_review
            ? `${AI_VERDICT[triage.ai_review.verdict]}${triage.ai_review.reason_en ? `: ${triage.ai_review.reason_en}` : "."}`
            : "AI check not available for this patient."}
        </p>
    </Popover>
  );
}
