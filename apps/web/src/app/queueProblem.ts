import type { TFunction } from "i18next";
import "../locales/staff";

type QueueProblem = {
  code?: string;
  active_token?: string;
  retry_after_seconds?: number;
};

/**
 * Staff wording for a queue rule the API refused (one patient per doctor, the call grace
 * period, another doctor's patient, an empty queue); null for anything else.
 */
export async function queueProblem(response: Response, t: TFunction): Promise<string | null> {
  let body: QueueProblem;
  try {
    body = (await response.json()) as QueueProblem;
  } catch {
    return null;
  }
  switch (body.code) {
    case "DOCTOR_BUSY":
      return t("doctorBusy", { ns: "staff", token: body.active_token ?? "" });
    case "CALL_GRACE_NOT_OVER":
      return t("graceNotOver", { ns: "staff", seconds: body.retry_after_seconds ?? 0 });
    case "CLAIMED_BY_ANOTHER_DOCTOR":
      return t("claimedByOther", { ns: "staff" });
    case "QUEUE_EMPTY":
      return t("queueEmpty", { ns: "staff" });
    default:
      return null;
  }
}
