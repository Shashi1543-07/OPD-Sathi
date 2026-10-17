import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Camera, FileText, Loader2, Trash2 } from "lucide-react";
import { intake, IntakeRequestError, type DocumentKind } from "../lib/intake";
import { shrinkPhoto } from "../lib/photos";

const KINDS: { kind: DocumentKind; label: string }[] = [
  { kind: "prescription", label: "paperPrescription" },
  { kind: "lab_report", label: "paperLabReport" },
  { kind: "discharge_summary", label: "paperDischarge" },
  { kind: "other", label: "paperOther" },
];
const KIND_LABELS = Object.fromEntries(KINDS.map(({ kind, label }) => [kind, label])) as Record<
  DocumentKind,
  string
>;

type Paper = { id: string; kind: DocumentKind; preview: string | null };

/**
 * Optional photos of old papers, on the review screen. The patient taps what the paper is and
 * takes its photo; each photo is kept with today's visit for the doctor and can be removed
 * until the summary is confirmed. With AI help the papers are also read for the doctor.
 */
export function KioskPapers({ online, disabled }: { online: boolean; disabled: boolean }) {
  const { t } = useTranslation("intake");
  const [papers, setPapers] = useState<Paper[]>([]);
  const [limit, setLimit] = useState(5);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const kind = useRef<DocumentKind>("other");
  const input = useRef<HTMLInputElement>(null);
  const previews = useRef<string[]>([]);

  useEffect(() => {
    // Photos added before the screen was reloaded are listed again (without a preview).
    let cancelled = false;
    void intake
      .documents()
      .then((result) => {
        if (cancelled) return;
        setLimit(result.limit);
        setPapers((current) =>
          current.length
            ? current
            : result.documents.map((item) => ({ id: item.id, kind: item.kind, preview: null })),
        );
      })
      .catch(() => undefined);
    const urls = previews.current;
    return () => {
      cancelled = true;
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, []);

  async function add(file: File) {
    setSaving(true);
    setMessage(t("papersSaving"));
    try {
      const photo = await shrinkPhoto(file);
      const added = await intake.addDocument(photo, kind.current);
      const preview = URL.createObjectURL(photo);
      previews.current.push(preview);
      setPapers((current) => [...current, { id: added.id, kind: added.kind, preview }]);
      setMessage(t("papersSaved"));
    } catch (cause) {
      const status = cause instanceof IntakeRequestError ? cause.status : 0;
      const code = cause instanceof IntakeRequestError ? cause.code : "";
      setMessage(
        t(
          code === "TOO_MANY_DOCUMENTS"
            ? "papersFull"
            : status === 413
              ? "scanTooLarge"
              : status === 415
                ? "papersNotPhoto"
                : cause instanceof TypeError
                  ? "connectionRetry"
                  : "papersFailed",
        ),
      );
    } finally {
      setSaving(false);
    }
  }

  async function remove(paper: Paper) {
    setSaving(true);
    try {
      await intake.removeDocument(paper.id);
      setMessage(t("papersRemoved"));
    } catch (cause) {
      // Already gone (for example removed from another tab): drop it here too.
      if (!(cause instanceof IntakeRequestError && cause.status === 404)) {
        setMessage(t("papersFailed"));
        return;
      }
    } finally {
      setSaving(false);
    }
    setPapers((current) => current.filter((item) => item.id !== paper.id));
  }

  const full = papers.length >= limit;
  return (
    <section className="intake-papers" aria-labelledby="intake-papers-title">
      <h2 id="intake-papers-title" className="intake-papers-title">
        <FileText aria-hidden="true" size={24} /> {t("papersTitle")}
      </h2>
      <p className="intake-papers-lead">{t("papersLead")}</p>
      <p className="intake-hint">
        {t(online ? "papersRead" : "papersNotRead")} {t("papersKept")}
      </p>
      {papers.length > 0 && (
        <ul className="intake-papers-list">
          {papers.map((paper, index) => {
            const label = t(KIND_LABELS[paper.kind]);
            return (
              <li key={paper.id}>
                {paper.preview ? (
                  <img
                    src={paper.preview}
                    alt={t("papersPhotoAlt", { number: index + 1, kind: label })}
                  />
                ) : (
                  <span className="intake-papers-icon">
                    <FileText aria-hidden="true" size={28} />
                  </span>
                )}
                <span className="intake-papers-kind">{label}</span>
                <button
                  type="button"
                  className="intake-btn intake-btn--ghost intake-btn--small"
                  disabled={saving || disabled}
                  aria-label={t("papersRemoveLabel", { number: index + 1, kind: label })}
                  onClick={() => void remove(paper)}
                >
                  <Trash2 aria-hidden="true" size={18} /> {t("papersRemove")}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {full ? (
        <p className="intake-hint">{t("papersFull")}</p>
      ) : (
        <>
          <p className="intake-papers-pick">{t("papersPick")}</p>
          <div className="intake-papers-kinds">
            {KINDS.map((item) => (
              <button
                key={item.kind}
                type="button"
                className="intake-btn intake-btn--secondary"
                disabled={saving || disabled}
                onClick={() => {
                  kind.current = item.kind;
                  input.current?.click();
                }}
              >
                <Camera aria-hidden="true" size={22} /> {t(item.label)}
              </button>
            ))}
          </div>
        </>
      )}
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        data-testid="papers-file"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void add(file);
        }}
      />
      <p className="intake-papers-status" aria-live="polite">
        {saving && <Loader2 className="spin" aria-hidden="true" size={18} />}
        {message || (papers.length ? t("papersCount", { count: papers.length, limit }) : "")}
      </p>
    </section>
  );
}
