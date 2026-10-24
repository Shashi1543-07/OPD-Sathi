import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import {
  Camera,
  CheckCircle2,
  FileText,
  History,
  ImageOff,
  Info,
  Loader2,
  Sparkles,
  XCircle,
} from "lucide-react";
import type { components } from "../lib/api-types";
import { staffAuth } from "../lib/auth";
import { shrinkPhoto } from "../lib/photos";
import type { StaffDocument, VisitDocuments } from "./visitDocuments";

type DocumentKind = StaffDocument["kind"];
type LabResult = components["schemas"]["LabResult"];
type Investigations = components["schemas"]["InvestigationsResponse"];

const KINDS: DocumentKind[] = ["prescription", "lab_report", "discharge_summary", "other"];

/**
 * Photos of old papers the patient brought (prescriptions, test reports, discharge papers),
 * each beside what AI read from it when the patient allowed AI help. What AI read is a draft
 * until the doctor compares it with the photo; results are flagged only against the range
 * printed on the same paper.
 */
export function DocumentsPanel({
  visitId,
  documents,
  failed,
  onChanged,
}: {
  visitId: string;
  documents: VisitDocuments | undefined;
  failed: boolean;
  onChanged: () => void;
}) {
  const { t } = useTranslation("staff");
  const role = staffAuth.user?.role;

  if (failed && !documents) {
    return (
      <p className="cx-none" role="alert">
        {t("papersLoadError")}
      </p>
    );
  }
  if (!documents) {
    return (
      <p className="cx-none" role="status">
        {t("papersLoading")}
      </p>
    );
  }

  return (
    <section className="dp" aria-labelledby={`dp-${visitId}`}>
      <div className="pv-head">
        <h3 id={`dp-${visitId}`}>
          <FileText aria-hidden="true" size={18} /> {t("papersTitle")}
        </h3>
      </div>
      {(role === "doctor" || role === "nurse") && (
        <AddPaper
          visitId={visitId}
          full={documents.documents.length >= (documents.limit ?? 10)}
          onAdded={onChanged}
        />
      )}
      <InvestigationsTimeline
        // A doctor's decision on a paper changes the timeline: load it again.
        key={[...documents.documents, ...documents.earlier]
          .map((paper) => `${paper.id}:${paper.review_status}`)
          .join(",")}
        visitId={visitId}
      />
      <section className="dp-group" aria-labelledby={`dp-today-${visitId}`}>
        <h4 id={`dp-today-${visitId}`}>{t("papersToday")}</h4>
        {documents.documents.length === 0 ? (
          <p className="cx-none">{t("papersNone")}</p>
        ) : (
          <ol className="dp-list">
            {documents.documents.map((paper) => (
              <PaperCard
                key={paper.id}
                visitId={visitId}
                paper={paper}
                canReview={role === "doctor"}
                onReviewed={onChanged}
              />
            ))}
          </ol>
        )}
      </section>
      <section className="dp-group" aria-labelledby={`dp-earlier-${visitId}`}>
        <h4 id={`dp-earlier-${visitId}`}>{t("papersEarlier")}</h4>
        {!documents.earlier_shown ? (
          <p className="cx-none">{t("papersEarlierClosed")}</p>
        ) : documents.earlier.length === 0 ? (
          <p className="cx-none">{t("papersEarlierNone")}</p>
        ) : (
          <ol className="dp-list">
            {documents.earlier.map((paper) => (
              <PaperCard key={paper.id} visitId={visitId} paper={paper} earlier />
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}

/**
 * Prior investigations (task 7.11.3): results a doctor accepted, from this and earlier visits,
 * oldest first, each with the range printed on its own paper. Unreviewed readings never appear.
 */
function InvestigationsTimeline({ visitId }: { visitId: string }) {
  const { t } = useTranslation("staff");
  const formatDate = useDate();
  const [data, setData] = useState<Investigations | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    staffAuth
      .request(`/visits/${visitId}/investigations`)
      .then(async (response) => {
        if (!response.ok) throw new Error("timeline unavailable");
        const body = (await response.json()) as Investigations;
        if (active) setData(body);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [visitId]);

  if (failed) {
    return (
      <p className="cx-none" role="alert">
        {t("timelineLoadError")}
      </p>
    );
  }
  if (!data) return null;
  const titleId = `dp-timeline-${visitId}`;
  return (
    <section className="dp-group dp-timeline" aria-labelledby={titleId}>
      <h4 id={titleId}>
        <History aria-hidden="true" size={16} /> {t("timelineTitle")}
      </h4>
      <p className="cx-panel-note">
        <Info aria-hidden="true" size={15} /> {t("timelineNote")}
      </p>
      {data.awaiting_review > 0 && (
        <p className="cx-panel-note">{t("timelineAwaiting", { count: data.awaiting_review })}</p>
      )}
      {data.papers.length === 0 ? (
        <p className="cx-none">{t("timelineNone")}</p>
      ) : (
        <>
          {data.tests.map((history) => (
            <div key={history.test} className="dp-results-wrap">
              <table className="dp-results">
                <caption>{history.test}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("timelineDate")}</th>
                    <th scope="col">{t("readingValue")}</th>
                    <th scope="col">{t("readingRange")}</th>
                    <th scope="col">{t("readingFlag")}</th>
                  </tr>
                </thead>
                <tbody>
                  {history.results.map(({ date, document_id, result }) => (
                    <tr
                      key={`${document_id}-${result.test}-${result.value}`}
                      className={
                        result.range_flag === "above" || result.range_flag === "below"
                          ? "is-outside"
                          : undefined
                      }
                    >
                      <th scope="row">{formatDate(date)}</th>
                      <td>{[result.value, result.unit].filter(Boolean).join(" ")}</td>
                      <td>{result.reference_range || "—"}</td>
                      <td>
                        <ResultFlags result={result} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          <h5>{t("timelinePapers")}</h5>
          <ol className="dp-timeline-papers">
            {data.papers.map((paper) => (
              <li key={paper.document_id}>
                <strong>{formatDate(paper.date)}</strong>
                {paper.date_source === "visit" && <small> ({t("timelineVisitDate")})</small>}
                {" · "}
                {t(`paperKind_${paper.kind}`)}
                {paper.facility && ` · ${paper.facility}`}
                {(paper.diagnoses ?? []).length > 0 && (
                  <span className="dp-timeline-detail">{(paper.diagnoses ?? []).join(", ")}</span>
                )}
                {(paper.medicines ?? []).length > 0 && (
                  <span className="dp-timeline-detail">
                    {(paper.medicines ?? []).map((medicine) => medicine.name).join(", ")}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function useDate() {
  const { i18n } = useTranslation("staff");
  return (iso: string) =>
    new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(i18n.language, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
}

function PaperCard({
  visitId,
  paper,
  canReview = false,
  earlier = false,
  onReviewed,
}: {
  visitId: string;
  paper: StaffDocument;
  canReview?: boolean;
  earlier?: boolean;
  onReviewed?: () => void;
}) {
  const { t } = useTranslation("staff");
  const date = useDate();
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const kind = t(`paperKind_${paper.kind}`);
  const reviewable =
    canReview &&
    !paper.ai_withdrawn &&
    paper.reading_status === "complete" &&
    paper.reading?.readable === true;

  async function review(decision: "accepted" | "rejected") {
    setBusy(true);
    setError("");
    try {
      const response = await staffAuth.request(
        `/visits/${visitId}/documents/${paper.id}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision }),
        },
      );
      if (!response.ok) throw new Error("review failed");
      onReviewed?.();
    } catch {
      setError(t("readingReviewFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={`cx-card dp-paper${expanded ? " is-expanded" : ""}`}>
      <button
        type="button"
        className="dp-photo"
        aria-expanded={expanded}
        aria-label={expanded ? t("paperClose", { kind }) : t("paperOpen", { kind })}
        onClick={() => setExpanded((value) => !value)}
      >
        <PaperPhoto
          path={`/visits/${visitId}/documents/${paper.id}/content`}
          alt={t("paperPhotoAlt", { kind })}
        />
      </button>
      <div className="dp-body">
        <div className="pv-visit-head">
          <strong>{kind}</strong>
          {earlier && (
            <span className="pv-chip pv-chip--muted">
              {t("paperVisit", { date: date(paper.service_date) })}
            </span>
          )}
          {paper.document_date && (
            <span className="pv-chip">{t("paperPrinted", { date: date(paper.document_date) })}</span>
          )}
        </div>
        <p className="dp-meta">
          {paper.source === "patient"
            ? t("paperAddedByPatient", { date: date(paper.added_at) })
            : t("paperAddedBy", { name: paper.added_by ?? t("aDoctor"), date: date(paper.added_at) })}
        </p>
        <Reading paper={paper} />
        {reviewable && (
          <div className="dp-actions">
            <button
              type="button"
              className="cx-btn"
              disabled={busy || paper.review_status === "accepted"}
              onClick={() => void review("accepted")}
            >
              <CheckCircle2 aria-hidden="true" size={15} /> {t("readingAccept")}
            </button>
            <button
              type="button"
              className="cx-btn"
              disabled={busy || paper.review_status === "rejected"}
              onClick={() => void review("rejected")}
            >
              <XCircle aria-hidden="true" size={15} /> {t("readingReject")}
            </button>
          </div>
        )}
        {error && (
          <p className="cx-none" role="alert">
            {error}
          </p>
        )}
      </div>
    </li>
  );
}

/** The photo, fetched with the staff sign-in; it never lands in the browser cache. */
function PaperPhoto({ path, alt }: { path: string; alt: string }) {
  const { t } = useTranslation("staff");
  const [photo, setPhoto] = useState<{ path: string; url: string | null }>();
  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    void staffAuth
      .request(path, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("photo unavailable");
        const blob = await response.blob();
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setPhoto({ path, url });
      })
      .catch(() => {
        if (!cancelled) setPhoto({ path, url: null });
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [path]);
  const current = photo?.path === path ? photo : undefined;
  if (!current) {
    return (
      <span className="dp-photo-placeholder" role="status">
        <Loader2 className="dp-spin" aria-hidden="true" size={20} />
        <span className="cx-visually-hidden">{t("papersLoading")}</span>
      </span>
    );
  }
  if (!current.url) {
    return (
      <span className="dp-photo-placeholder">
        <ImageOff aria-hidden="true" size={20} /> {t("paperPhotoError")}
      </span>
    );
  }
  return <img src={current.url} alt={alt} />;
}

function Reading({ paper }: { paper: StaffDocument }) {
  const { t } = useTranslation("staff");
  if (paper.ai_withdrawn) return <p className="dp-status">{t("readingWithdrawn")}</p>;
  if (paper.reading_status === "pending") {
    return (
      <p className="dp-status" role="status">
        <Loader2 className="dp-spin" aria-hidden="true" size={15} /> {t("readingPending")}
      </p>
    );
  }
  if (paper.reading_status === "skipped") return <p className="dp-status">{t("readingSkipped")}</p>;
  const reading = paper.reading;
  if (paper.reading_status === "failed" || !reading) {
    return <p className="dp-status">{t("readingFailed")}</p>;
  }
  if (!reading.readable) return <p className="dp-status">{t("readingUnreadable")}</p>;
  const reviewer = paper.reviewed_by ?? t("aDoctor");
  const label =
    paper.review_status === "accepted"
      ? t("readingAccepted", { name: reviewer })
      : paper.review_status === "rejected"
        ? t("readingRejected", { name: reviewer })
        : t("readingDraft");
  const diagnoses = reading.diagnoses ?? [];
  const medicines = reading.medicines ?? [];
  const results = reading.lab_results ?? [];
  const procedures = reading.procedures ?? [];
  return (
    <div className={`dp-reading dp-reading--${paper.review_status}`}>
      <span className={paper.review_status === "accepted" ? "cx-verified-chip" : "cx-ai-chip"}>
        <Sparkles aria-hidden="true" size={11} /> {label}
      </span>
      {/* A rejected reading is not shown: the photo is the record. */}
      {paper.review_status !== "rejected" && (
        <>
          {reading.facility && (
            <p className="dp-line">
              <span className="dp-label">{t("readingFacility")}</span> {reading.facility}
            </p>
          )}
          {diagnoses.length > 0 && (
            <>
              <h5>{t("readingDiagnoses")}</h5>
              <ul className="dp-items">
                {diagnoses.map((item, index) => (
                  <li key={`${index}-${item}`}>{item}</li>
                ))}
              </ul>
            </>
          )}
          {medicines.length > 0 && (
            <>
              <h5>{t("readingMedicines")}</h5>
              <ul className="dp-items">
                {medicines.map((item, index) => (
                  <li key={`${index}-${item.name}`}>
                    {[item.name, item.dose, item.how_often, item.duration]
                      .filter(Boolean)
                      .join(" · ")}
                  </li>
                ))}
              </ul>
            </>
          )}
          {results.length > 0 && (
            <>
              <h5>{t("readingResults")}</h5>
              <div className="dp-results-wrap">
                <table className="dp-results">
                  <thead>
                    <tr>
                      <th scope="col">{t("readingTest")}</th>
                      <th scope="col">{t("readingValue")}</th>
                      <th scope="col">{t("readingRange")}</th>
                      <th scope="col">{t("readingFlag")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((result, index) => (
                      <tr
                        key={`${index}-${result.test}`}
                        className={
                          result.range_flag === "above" || result.range_flag === "below"
                            ? "is-outside"
                            : undefined
                        }
                      >
                        <th scope="row">{result.test}</th>
                        <td>{[result.value, result.unit].filter(Boolean).join(" ")}</td>
                        <td>{result.reference_range || "—"}</td>
                        <td>
                          <ResultFlags result={result} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="cx-panel-note">
                <Info aria-hidden="true" size={15} /> {t("flagsNote")}
              </p>
            </>
          )}
          {procedures.length > 0 && (
            <>
              <h5>{t("readingProcedures")}</h5>
              <ul className="dp-items">
                {procedures.map((item, index) => (
                  <li key={`${index}-${item}`}>{item}</li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}

function ResultFlags({ result }: { result: LabResult }) {
  const { t } = useTranslation("staff");
  const flags = [
    result.range_flag === "above"
      ? t("flagAbove")
      : result.range_flag === "below"
        ? t("flagBelow")
        : result.range_flag === "within"
          ? t("flagWithin")
          : null,
    result.printed_flag === "high"
      ? t("flagPrintedHigh")
      : result.printed_flag === "low"
        ? t("flagPrintedLow")
        : null,
  ].filter((flag): flag is string => Boolean(flag));
  return flags.length ? <>{flags.join(" · ")}</> : <>—</>;
}

/** A doctor or nurse photographs a paper the patient shows at the desk or in the room. */
function AddPaper({
  visitId,
  full,
  onAdded,
}: {
  visitId: string;
  full: boolean;
  onAdded: () => void;
}) {
  const { t } = useTranslation("staff");
  const [kind, setKind] = useState<DocumentKind>("prescription");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const input = useRef<HTMLInputElement>(null);

  async function add(file: File) {
    setBusy(true);
    setMessage(t("paperAdding"));
    try {
      const photo = await shrinkPhoto(file);
      const response = await staffAuth.request(`/visits/${visitId}/documents?kind=${kind}`, {
        method: "POST",
        headers: { "Content-Type": photo.type || "image/jpeg" },
        body: photo,
      });
      if (!response.ok) {
        const problem = (await response.json().catch(() => ({}))) as { code?: string };
        setMessage(
          t(
            problem.code === "TOO_MANY_DOCUMENTS"
              ? "paperAddFull"
              : problem.code === "VISIT_CLOSED"
                ? "paperAddClosed"
                : response.status === 413
                  ? "paperTooLarge"
                  : response.status === 415
                    ? "paperNotPhoto"
                    : "paperAddFailed",
          ),
        );
        return;
      }
      setMessage(t("paperAdded"));
      onAdded();
    } catch {
      setMessage(t("paperAddFailed"));
    } finally {
      setBusy(false);
    }
  }

  if (full) return <p className="cx-none">{t("paperAddFull")}</p>;
  return (
    <div className="dp-add">
      <label className="dp-add-kind">
        {t("paperAddKind")}
        <select
          value={kind}
          disabled={busy}
          onChange={(event) => setKind(event.target.value as DocumentKind)}
        >
          {KINDS.map((item) => (
            <option key={item} value={item}>
              {t(`paperKind_${item}`)}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="cx-btn"
        disabled={busy}
        onClick={() => input.current?.click()}
      >
        {busy ? (
          <Loader2 className="dp-spin" aria-hidden="true" size={15} />
        ) : (
          <Camera aria-hidden="true" size={15} />
        )}
        {t("paperAdd")}
      </button>
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        data-testid="paper-file"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void add(file);
        }}
      />
      <p className="dp-add-status" role="status">
        {message}
      </p>
    </div>
  );
}
