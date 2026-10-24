import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { IdCard, Search } from "lucide-react";
import "../locales/staff";
import { staffAuth } from "../lib/auth";

type Candidate = {
  opd_number: string;
  age_years: number | null;
  sex: string;
  visit_count: number;
  last_visit: string | null;
  card_version: number;
};
type NewCard = { opd_number: string; pin: string; card_version: number };

/**
 * A patient who lost their OPD card gets a new PIN at the desk (task 7.11.4). Staff find the
 * record, check age and sex with the patient, then re-issue: the number and records stay the
 * same and the old PIN stops working. The new PIN is shown once, to write on a new card.
 */
export function OpdCardReissue() {
  const { t, i18n } = useTranslation("staff");
  const titleId = useId();
  const [mode, setMode] = useState<"opd_number" | "phone">("opd_number");
  const [term, setTerm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [confirming, setConfirming] = useState<Candidate | null>(null);
  const [issued, setIssued] = useState<NewCard | null>(null);

  function reset() {
    setTerm("");
    setCandidates(null);
    setConfirming(null);
    setIssued(null);
    setError("");
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setConfirming(null);
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

  async function reissue(candidate: Candidate) {
    setBusy(true);
    setError("");
    try {
      const response = await staffAuth.request("/patients/card/reissue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          opd_number: candidate.opd_number,
          card_version: candidate.card_version,
        }),
      });
      if (response.status === 409) throw new Error(t("reissueChanged"));
      if (!response.ok) throw new Error(t("reissueFailed"));
      setIssued((await response.json()) as NewCard);
      setCandidates(null);
      setConfirming(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("reissueFailed"));
    } finally {
      setBusy(false);
    }
  }

  const describe = (candidate: Candidate) =>
    t("linkCandidate", {
      age: candidate.age_years ?? "—",
      sex: t(`sex_${candidate.sex}`, { defaultValue: candidate.sex }),
      count: candidate.visit_count,
      date: candidate.last_visit
        ? new Date(`${candidate.last_visit}T00:00:00`).toLocaleDateString(i18n.language, {
            day: "numeric",
            month: "short",
          })
        : "—",
    });

  return (
    <article className="pr-register pr-reissue" aria-labelledby={titleId}>
      <IdCard aria-hidden="true" size={26} />
      <div className="pr-register-text">
        <h3 id={titleId}>{t("reissueTitle")}</h3>
        <p>{t("reissueHint")}</p>
      </div>
      {issued ? (
        <div className="pr-reissue-result" role="status">
          <p>
            {t("reissueResult", { number: issued.opd_number })}{" "}
            <strong className="pr-reissue-pin">{issued.pin}</strong>
          </p>
          <p>{t("reissueResultHint")}</p>
          <button type="button" className="pr-btn" onClick={reset}>
            {t("reissueDone")}
          </button>
        </div>
      ) : (
        <>
          <form className="pr-reissue-form" onSubmit={(event) => void search(event)}>
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
            <button type="submit" className="pr-btn" disabled={busy || term.trim().length < 6}>
              <Search aria-hidden="true" size={15} /> {t("linkSearch")}
            </button>
          </form>
          {candidates !== null &&
            (candidates.length === 0 ? (
              <p className="pr-reissue-note">{t("linkNoMatch")}</p>
            ) : (
              <ul className="pr-reissue-candidates">
                {candidates.map((candidate) => (
                  <li key={candidate.opd_number}>
                    <span>
                      <strong>{candidate.opd_number}</strong> {describe(candidate)}
                    </span>
                    {confirming?.opd_number === candidate.opd_number ? (
                      <span className="pr-reissue-confirm">
                        <span>{t("reissueConfirm")}</span>
                        <button
                          type="button"
                          className="pr-btn pr-btn--primary"
                          disabled={busy}
                          onClick={() => void reissue(candidate)}
                        >
                          {t("reissueConfirmAction")}
                        </button>
                        <button type="button" className="pr-btn" onClick={() => setConfirming(null)}>
                          {t("reissueCancel")}
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="pr-btn"
                        disabled={busy}
                        onClick={() => setConfirming(candidate)}
                      >
                        {t("reissueAction")}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            ))}
        </>
      )}
      {error && (
        <p className="pr-error" role="alert">
          {error}
        </p>
      )}
    </article>
  );
}
