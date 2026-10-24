import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { History, Info, Stethoscope } from "lucide-react";
import "../locales/staff";
import type { components } from "../lib/api-types";
import { staffAuth } from "../lib/auth";

type Notes = components["schemas"]["DoctorNotesResponse"];
type NoteVersion = components["schemas"]["DoctorNotesVersion"];
type Draft = { findings: string; assessment: string; plan: string; follow_up_on: string };
type SaveState = "idle" | "saving" | "saved" | "failed" | "conflict" | "closed";

const PARTS = ["findings", "assessment", "plan"] as const;
const LABELS = {
  findings: "doctorNotesFindings",
  assessment: "doctorNotesAssessment",
  plan: "doctorNotesPlan",
} as const;
const emptyDraft: Draft = { findings: "", assessment: "", plan: "", follow_up_on: "" };

function draftOf(version: NoteVersion | undefined): Draft {
  if (!version) return emptyDraft;
  return {
    findings: version.findings,
    assessment: version.assessment,
    plan: version.plan,
    follow_up_on: version.follow_up_on ?? "",
  };
}

/**
 * The doctor's own findings, assessment, plan and follow-up date (task 7.11.1).
 * Each save is a new version; earlier versions stay readable. Never written by AI.
 * When someone else saved meanwhile, the doctor's text is kept and their version shown.
 */
export function DoctorNotesPanel({ visitId }: { visitId: string }) {
  const { t, i18n } = useTranslation("staff");
  const baseId = useId();
  const [notes, setNotes] = useState<Notes | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [state, setState] = useState<SaveState>("idle");
  const isDoctor = staffAuth.user?.role === "doctor";

  const load = useCallback(
    async (keepDraft = false) => {
      setLoadFailed(false);
      try {
        const response = await staffAuth.request(`/visits/${visitId}/doctor-notes`);
        if (!response.ok) throw new Error("notes unavailable");
        const loaded = (await response.json()) as Notes;
        setNotes(loaded);
        if (!keepDraft) setDraft(draftOf(loaded.versions?.[0]));
      } catch {
        setLoadFailed(true);
      }
    },
    [visitId],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const versions = notes?.versions ?? [];
  const current = draftOf(versions[0]);
  const trimmed = {
    findings: draft.findings.trim(),
    assessment: draft.assessment.trim(),
    plan: draft.plan.trim(),
    follow_up_on: draft.follow_up_on,
  };
  const hasText = Boolean(trimmed.findings || trimmed.assessment || trimmed.plan);
  const changed =
    PARTS.some((part) => trimmed[part] !== current[part]) ||
    trimmed.follow_up_on !== current.follow_up_on;
  const canEdit = isDoctor && notes?.editable === true;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!notes || !hasText || !changed) return;
    setState("saving");
    try {
      const response = await staffAuth.request(`/visits/${visitId}/doctor-notes`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "If-Match": `"${notes.version}"` },
        body: JSON.stringify({ ...trimmed, follow_up_on: trimmed.follow_up_on || null }),
      });
      if (response.status === 409) {
        const problem = (await response.json().catch(() => ({}))) as { code?: string };
        setState(problem.code === "NOTES_CLOSED" ? "closed" : "conflict");
        await load(true);
        return;
      }
      if (!response.ok) throw new Error("notes not saved");
      const saved = (await response.json()) as Notes;
      setNotes(saved);
      setDraft(draftOf(saved.versions?.[0]));
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  const when = (value: string) =>
    new Date(value).toLocaleString(i18n.language === "en" ? "en-IN" : `${i18n.language}-IN`, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  const byline = (version: NoteVersion) =>
    t("doctorNotesBy", { version: version.version, name: version.by_name, at: when(version.at) });

  if (loadFailed) {
    return (
      <section className="cx-card" aria-labelledby={`${baseId}-title`}>
        <h4 id={`${baseId}-title`}>{t("notesTitle")}</h4>
        <p className="cx-error" role="alert">
          {t("doctorNotesLoadFailed")}
        </p>
        <button type="button" className="cx-btn" onClick={() => void load()}>
          {t("retry")}
        </button>
      </section>
    );
  }
  if (!notes) {
    return (
      <p className="cx-none" role="status">
        {t("doctorNotesLoading")}
      </p>
    );
  }

  const [latest, ...earlier] = versions;
  return (
    <section className="cx-card dn" aria-labelledby={`${baseId}-title`}>
      <h4 id={`${baseId}-title`}>
        <Stethoscope aria-hidden="true" size={16} /> {t("notesTitle")}
      </h4>
      <p className="cx-panel-note">
        <Info aria-hidden="true" size={15} />
        {t("doctorNotesIntro")}
      </p>
      {canEdit ? (
        <form className="dn-form" onSubmit={(event) => void save(event)}>
          {PARTS.map((part) => (
            <label key={part} className="dn-field">
              <span>{t(LABELS[part])}</span>
              <textarea
                value={draft[part]}
                maxLength={4000}
                rows={part === "assessment" ? 2 : 4}
                onChange={(event) => {
                  setDraft((value) => ({ ...value, [part]: event.target.value }));
                  setState((value) => (value === "conflict" ? value : "idle"));
                }}
              />
            </label>
          ))}
          <label className="dn-field dn-field--date">
            <span>{t("doctorNotesFollowUp")}</span>
            <input
              type="date"
              value={draft.follow_up_on}
              onChange={(event) => {
                setDraft((value) => ({ ...value, follow_up_on: event.target.value }));
                setState((value) => (value === "conflict" ? value : "idle"));
              }}
            />
          </label>
          {state === "saved" && latest && (
            <p className="cx-strip-ok" role="status">
              {t("doctorNotesSaved", { version: latest.version })}
            </p>
          )}
          {state === "failed" && (
            <p className="cx-error" role="alert">
              {t("doctorNotesFailed")}
            </p>
          )}
          {state === "conflict" && (
            <p className="cx-error" role="alert">
              {t("doctorNotesConflict")}
            </p>
          )}
          {!hasText && changed && (
            <p className="cx-panel-note">{t("doctorNotesNeedText")}</p>
          )}
          <button
            type="submit"
            className="cx-btn cx-btn--primary"
            disabled={state === "saving" || !hasText || !changed}
          >
            {t("doctorNotesSave")}
          </button>
        </form>
      ) : (
        <>
          {state === "closed" || (isDoctor && !notes.editable) ? (
            <p className="cx-panel-note">{t("doctorNotesClosed")}</p>
          ) : (
            !isDoctor && <p className="cx-panel-note">{t("doctorNotesReadOnly")}</p>
          )}
          {latest ? <NoteView version={latest} byline={byline(latest)} /> : null}
        </>
      )}
      {!latest && <p className="cx-none">{t("doctorNotesEmpty")}</p>}
      {canEdit && latest && <p className="cx-audit">{byline(latest)}</p>}
      {state === "conflict" && latest && (
        <div className="dn-theirs">
          <NoteView version={latest} byline={byline(latest)} />
        </div>
      )}
      {earlier.length > 0 && (
        <details className="dn-earlier">
          <summary>
            <History aria-hidden="true" size={14} />{" "}
            {t("doctorNotesEarlier", { count: earlier.length })}
          </summary>
          {earlier.map((version) => (
            <NoteView key={version.version} version={version} byline={byline(version)} />
          ))}
        </details>
      )}
    </section>
  );
}

function NoteView({ version, byline }: { version: NoteVersion; byline: string }) {
  const { t } = useTranslation("staff");
  return (
    <article className="dn-version">
      <p className="cx-audit">{byline}</p>
      <dl>
        {PARTS.filter((part) => version[part]).map((part) => (
          <div key={part} className="cx-field">
            <dt>{t(LABELS[part])}</dt>
            <dd className="dn-text">{version[part]}</dd>
          </div>
        ))}
        {version.follow_up_on && (
          <div className="cx-field">
            <dt>{t("doctorNotesFollowUp")}</dt>
            <dd>{version.follow_up_on}</dd>
          </div>
        )}
      </dl>
    </article>
  );
}
