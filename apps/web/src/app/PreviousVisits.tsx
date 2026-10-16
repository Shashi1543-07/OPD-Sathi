import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { History, IdCard, Info, Link2, Search, ShieldAlert, Sparkles } from "lucide-react";
import { staffAuth } from "../lib/auth";
import { newRequestId } from "../lib/ids";

type PreviousVisit = {
  visit_id: string;
  service_date: string;
  department: string;
  status: string;
  token: string | null;
  urgency: string;
  chief_complaint: string | null;
  ai_brief: string | null;
  staff_edited: boolean;
  red_flags: string[];
  conditions: string[];
  medications: string[];
  allergies: string[];
  no_known_allergies: boolean;
};
type KnownItem = { text: string; last_reported: string; visits: number };
export type PatientHistory = {
  visit_id: string;
  opd_number: string | null;
  linked_by: "card" | "staff" | null;
  follow_up_of: string | null;
  previous_visits: PreviousVisit[];
  known_allergies: KnownItem[];
  known_medications: KnownItem[];
  known_conditions: KnownItem[];
  no_known_allergies_reported: string | null;
};
type Candidate = {
  opd_number: string;
  age_years: number | null;
  sex: string;
  visit_count: number;
  last_visit: string | null;
};

/**
 * Today's patient's earlier visits, kept apart from today's interview. Everything here was
 * recorded at those visits (mostly patient-reported); the doctor confirms it today.
 */
export function PreviousVisits({
  visitId,
  history,
  failed,
  onLinked,
}: {
  visitId: string;
  history: PatientHistory | undefined;
  failed: "error" | "closed" | null;
  onLinked: () => void;
}) {
  const { t, i18n } = useTranslation("staff");
  const date = (iso: string) =>
    new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(i18n.language, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });

  if (failed === "closed") {
    return <p className="cx-none">{t("previousClosed")}</p>;
  }
  if (failed && !history) {
    return (
      <p className="cx-none" role="alert">
        {t("previousLoadError")}
      </p>
    );
  }
  if (!history) {
    return (
      <p className="cx-none" role="status">
        {t("previousLoading")}
      </p>
    );
  }

  const followUp = history.previous_visits.find((visit) => visit.visit_id === history.follow_up_of);
  const known = (items: KnownItem[], fallback: string | null = null) =>
    items.length ? (
      <ul className="pv-known-list">
        {items.map((item) => (
          <li key={item.text}>
            <span>{item.text}</span>
            <small>
              {t("previousLastReported", { date: date(item.last_reported), count: item.visits })}
            </small>
          </li>
        ))}
      </ul>
    ) : (
      <p className="cx-none">{fallback ?? t("previousNotRecorded")}</p>
    );

  return (
    <section className="pv" aria-labelledby={`pv-${visitId}`}>
      <div className="pv-head">
        <h3 id={`pv-${visitId}`}>
          <History aria-hidden="true" size={18} /> {t("previousTitle")}
        </h3>
        {history.opd_number && (
          <span className="pv-chip">
            <IdCard aria-hidden="true" size={14} /> {t("previousOpdNumber", { number: history.opd_number })}
          </span>
        )}
        {history.linked_by && (
          <span className="pv-chip pv-chip--muted">
            {history.linked_by === "card" ? t("previousLinkedCard") : t("previousLinkedStaff")}
          </span>
        )}
      </div>
      {followUp && (
        <p className="pv-followup">
          {t("previousFollowUp", {
            date: date(followUp.service_date),
            complaint: followUp.chief_complaint ?? t("previousNotRecorded"),
          })}
        </p>
      )}

      {history.previous_visits.length === 0 ? (
        <>
          <p className="cx-none">{t("previousNone")}</p>
          <LinkEarlierRecord visitId={visitId} onLinked={onLinked} />
        </>
      ) : (
        <>
          <section className="cx-card pv-known" aria-label={t("previousKnownTitle")}>
            <h4>{t("previousKnownTitle")}</h4>
            <p className="cx-panel-note">
              <Info aria-hidden="true" size={15} /> {t("previousKnownNote")}
            </p>
            <div className="pv-known-grid">
              <div>
                <h5>{t("allergies")}</h5>
                {known(
                  history.known_allergies,
                  history.no_known_allergies_reported
                    ? t("previousNoKnownAllergy", {
                        date: date(history.no_known_allergies_reported),
                      })
                    : null,
                )}
              </div>
              <div>
                <h5>{t("medications")}</h5>
                {known(history.known_medications)}
              </div>
              <div>
                <h5>{t("pastHistory")}</h5>
                {known(history.known_conditions)}
              </div>
            </div>
          </section>
          <ol className="pv-visits">
            {history.previous_visits.map((visit) => (
              <li key={visit.visit_id} className="cx-card pv-visit">
                <div className="pv-visit-head">
                  <strong>{date(visit.service_date)}</strong>
                  <span>{visit.department}</span>
                  {visit.token && <span className="pv-token">{visit.token}</span>}
                  {visit.staff_edited && (
                    <span className="pv-chip pv-chip--muted">{t("previousStaffEdited")}</span>
                  )}
                </div>
                <p className="pv-complaint">{visit.chief_complaint ?? t("previousNotRecorded")}</p>
                {visit.red_flags.length > 0 && (
                  <p className="pv-flags">
                    <ShieldAlert aria-hidden="true" size={15} />
                    {t("previousRedFlags")}: {visit.red_flags.join("; ")}
                  </p>
                )}
                {visit.ai_brief && (
                  <p className="pv-brief">
                    <span className="cx-ai-chip">
                      <Sparkles aria-hidden="true" size={11} /> {t("previousAiBrief")}
                    </span>{" "}
                    {visit.ai_brief}
                  </p>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

/** For a patient without their OPD card: find the earlier record and link it after checking. */
function LinkEarlierRecord({ visitId, onLinked }: { visitId: string; onLinked: () => void }) {
  const { t, i18n } = useTranslation("staff");
  const role = staffAuth.user?.role;
  const [mode, setMode] = useState<"opd_number" | "phone">("opd_number");
  const [term, setTerm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);

  if (role !== "nurse" && role !== "admin") {
    return <p className="pv-hint">{t("linkDoctorNote")}</p>;
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const query = new URLSearchParams({ [mode]: term.trim() });
      const response = await staffAuth.request(`/patients/lookup?${query}`);
      if (!response.ok) throw new Error(t("linkSearchFailed"));
      setCandidates(((await response.json()) as { items: Candidate[] }).items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("linkSearchFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function link(candidate: Candidate) {
    setBusy(true);
    setError("");
    try {
      const response = await staffAuth.request(`/queue/${visitId}/link-patient`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": newRequestId() },
        body: JSON.stringify({ opd_number: candidate.opd_number }),
      });
      if (!response.ok) {
        const problem = (await response.json().catch(() => ({}))) as { detail?: string };
        throw new Error(problem.detail ?? t("linkFailed"));
      }
      onLinked();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("linkFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="cx-card pv-link" aria-labelledby={`pv-link-${visitId}`}>
      <h4 id={`pv-link-${visitId}`}>
        <Search aria-hidden="true" size={16} /> {t("linkTitle")}
      </h4>
      <p className="cx-panel-note">{t("linkHint")}</p>
      <form className="pv-link-form" onSubmit={(event) => void search(event)}>
        <select
          aria-label={t("linkSearchBy")}
          value={mode}
          onChange={(event) => {
            setMode(event.target.value as "opd_number" | "phone");
            setCandidates(null);
          }}
        >
          <option value="opd_number">{t("linkSearchNumber")}</option>
          <option value="phone">{t("linkSearchPhone")}</option>
        </select>
        <input
          aria-label={mode === "phone" ? t("linkSearchPhone") : t("linkSearchNumber")}
          inputMode="numeric"
          value={term}
          maxLength={mode === "phone" ? 10 : 14}
          onChange={(event) => setTerm(event.target.value)}
        />
        <button type="submit" className="cx-btn" disabled={busy || term.trim().length < 6}>
          {t("linkSearch")}
        </button>
      </form>
      {candidates !== null &&
        (candidates.length === 0 ? (
          <p className="cx-none">{t("linkNoMatch")}</p>
        ) : (
          <ul className="pv-candidates">
            {candidates.map((candidate) => (
              <li key={candidate.opd_number}>
                <span>
                  <strong>{candidate.opd_number}</strong>{" "}
                  {t("linkCandidate", {
                    age: candidate.age_years ?? "—",
                    sex: t(`sex_${candidate.sex}`, { defaultValue: candidate.sex }),
                    count: candidate.visit_count,
                    date: candidate.last_visit
                      ? new Date(`${candidate.last_visit}T00:00:00`).toLocaleDateString(
                          i18n.language,
                          { day: "numeric", month: "short" },
                        )
                      : "—",
                  })}
                </span>
                <button
                  type="button"
                  className="cx-btn"
                  disabled={busy}
                  onClick={() => void link(candidate)}
                >
                  <Link2 aria-hidden="true" size={15} /> {t("linkAction")}
                </button>
              </li>
            ))}
          </ul>
        ))}
      {error && (
        <p className="cx-none" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
