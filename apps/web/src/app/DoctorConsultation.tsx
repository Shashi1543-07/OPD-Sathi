import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { components } from "../lib/api-types";
import { staffAuth } from "../lib/auth";

type CaseSheet = components["schemas"]["CaseSheet"];
type TranscriptTurn = {
  turn_index: number;
  question_id: string;
  input_mode: string;
  text_original: string | null;
  text_english: string | null;
  stt_confidence: number | null;
};
type CaseSheetResult = { version: number; case_sheet: CaseSheet };

const emptyText = "—";

export function DoctorConsultation() {
  const { t } = useTranslation("staff");
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const visitId = searchParams.get("visit_id");
  const routeStatus = searchParams.get("status");
  const [actionState, setActionState] = useState<{
    visitId: string;
    status: string;
  } | null>(null);
  const visitStatus =
    actionState?.visitId === visitId ? actionState.status : routeStatus;
  const [sheet, setSheet] = useState<CaseSheetResult>();
  const [transcript, setTranscript] = useState<TranscriptTurn[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<{ path: string; value: string }>();
  const [saving, setSaving] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [conflict, setConflict] = useState(false);
  const firstEditRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(async (): Promise<CaseSheetResult | undefined> => {
    if (!visitId) {
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    setError("");
    try {
      const [sheetResponse, transcriptResponse] = await Promise.all([
        staffAuth.request(`/visits/${visitId}/case-sheet`),
        staffAuth.request(`/visits/${visitId}/transcript`),
      ]);
      if (!sheetResponse.ok || !transcriptResponse.ok) {
        throw new Error(t("consultLoadError"));
      }
      const body = (await sheetResponse.json()) as CaseSheetResult;
      const transcriptBody = (await transcriptResponse.json()) as {
        turns: TranscriptTurn[];
      };
      const etag = sheetResponse.headers.get("ETag");
      const headerVersion = Number(etag?.replaceAll('"', ""));
      setSheet({
        ...body,
        version: Number.isFinite(headerVersion) ? headerVersion : body.version,
      });
      setTranscript(transcriptBody.turns);
      setConflict(false);
      return {
        ...body,
        version: Number.isFinite(headerVersion) ? headerVersion : body.version,
      };
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : t("consultLoadError"),
      );
      return undefined;
    } finally {
      setLoading(false);
    }
  }, [visitId, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      )
        return;
      if (event.key.toLowerCase() === "n") navigate("/staff/queue");
      if (event.key.toLowerCase() === "e") firstEditRef.current?.focus();
      if (event.key.toLowerCase() === "p") window.print();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  async function saveEdit(versionOverride?: number) {
    if (!visitId || !sheet || !draft) return;
    setSaving(true);
    setError("");
    try {
      const patchValue =
        draft.path === "/hpi/severity_0_10" ? Number(draft.value) : draft.value;
      const response = await staffAuth.request(
        `/visits/${visitId}/case-sheet`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json-patch+json",
            "If-Match": `"${versionOverride ?? sheet.version}"`,
          },
          body: JSON.stringify([
            { op: "replace", path: draft.path, value: patchValue },
          ]),
        },
      );
      if (response.status === 409) {
        setConflict(true);
        return;
      }
      if (!response.ok) throw new Error(t("consultSaveError"));
      const updated = (await response.json()) as CaseSheetResult;
      const etag = response.headers.get("ETag");
      const headerVersion = Number(etag?.replaceAll('"', ""));
      setSheet({
        ...updated,
        version: Number.isFinite(headerVersion)
          ? headerVersion
          : updated.version,
      });
      setDraft(undefined);
      setConflict(false);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : t("consultSaveError"),
      );
    } finally {
      setSaving(false);
    }
  }

  async function transition(action: "start" | "complete") {
    if (!visitId) return;
    try {
      const response = await staffAuth.request(`/queue/${visitId}/${action}`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(t("consultActionError"));
      setActionState({
        visitId,
        status: action === "start" ? "in_consult" : "done",
      });
      if (action === "complete") navigate("/staff/queue");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : t("consultActionError"),
      );
    }
  }

  async function exportPdf() {
    if (!visitId) return;
    setExportingPdf(true);
    setError("");
    try {
      const response = await staffAuth.request(
        `/visits/${visitId}/case-sheet.pdf`,
      );
      if (!response.ok) throw new Error(t("pdfExportError"));
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `opd-saathi-${visitId}.pdf`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("pdfExportError"));
    } finally {
      setExportingPdf(false);
    }
  }

  function field(
    label: string,
    value: string | number | null | undefined,
    path: string,
    index: number,
    editable = true,
  ) {
    const text =
      value === null || value === undefined || value === ""
        ? emptyText
        : String(value);
    const metadata = sheet?.case_sheet.field_meta?.[path];
    const editing = draft?.path === path;
    return (
      <div className="consult-field" key={path}>
        <dt>{label}</dt>
        <dd>
          <div className="consult-field-value">
            {editing ? (
              <textarea
                aria-label={label}
                value={draft.value}
                onChange={(event) =>
                  setDraft({ path, value: event.target.value })
                }
              />
            ) : (
              text
            )}
          </div>
          <small>
            {metadata
              ? `${metadata.source} · ${Math.round(metadata.confidence * 100)}%`
              : t("sourceUnrecorded")}
          </small>
          {editing ? (
            <div className="consult-field-actions">
              <button
                type="button"
                onClick={() => void saveEdit()}
                disabled={saving || !draft.value.trim()}
              >
                {t("saveEdit")}
              </button>
              <button type="button" onClick={() => setDraft(undefined)}>
                {t("cancelEdit")}
              </button>
            </div>
          ) : editable ? (
            <button
              ref={index === 0 ? firstEditRef : undefined}
              type="button"
              className="consult-edit"
              onClick={() =>
                setDraft({ path, value: text === emptyText ? "" : text })
              }
            >
              {t("editField")}
            </button>
          ) : null}
        </dd>
      </div>
    );
  }

  if (!visitId) {
    return (
      <section className="doctor-consult">
        <h2>{t("consultations")}</h2>
        <p role="status">{t("chooseVisit")}</p>
        <Link to="/staff/queue">{t("backToQueue")}</Link>
      </section>
    );
  }
  if (loading)
    return (
      <section className="doctor-consult">
        <p role="status">{t("loadingConsult")}</p>
      </section>
    );
  if (error && !sheet)
    return (
      <section className="doctor-consult">
        <p role="alert">{error}</p>
        <button type="button" onClick={() => void load()}>
          {t("retry")}
        </button>
      </section>
    );
  if (!sheet)
    return (
      <section className="doctor-consult">
        <p role="status">{t("caseSheetUnavailable")}</p>
      </section>
    );

  const data = sheet.case_sheet;
  const associatedSymptoms = data.hpi.associated_symptoms ?? [];
  const redFlags = data.red_flags ?? [];
  const conditions = data.past_history?.conditions ?? [];
  const medications = data.medications ?? [];
  const allergies = data.allergies ?? [];
  const summaryFields = [
    field(
      t("chiefComplaint"),
      data.chief_complaint.text_original,
      "/chief_complaint/text_original",
      0,
    ),
    field(
      t("duration"),
      data.hpi.onset?.text_original,
      "/hpi/onset/text_original",
      1,
      Boolean(data.hpi.onset),
    ),
    field(
      t("severity"),
      data.hpi.severity_0_10,
      "/hpi/severity_0_10",
      2,
      typeof data.hpi.severity_0_10 === "number",
    ),
  ];
  const additionalFields = [
    ...associatedSymptoms.map((item, index) =>
      field(
        t("associatedSymptoms"),
        item.text_original,
        `/hpi/associated_symptoms/${index}/text_original`,
        index + 3,
      ),
    ),
    ...conditions.map((item, index) =>
      field(
        t("pastHistory"),
        item,
        `/past_history/conditions/${index}`,
        index + 40,
      ),
    ),
    ...(data.past_history?.surgeries ?? []).map((item, index) =>
      field(
        t("pastHistory"),
        item,
        `/past_history/surgeries/${index}`,
        index + 70,
      ),
    ),
    ...medications.map((item, index) =>
      field(
        t("medications"),
        item.name,
        `/medications/${index}/name`,
        index + 100,
      ),
    ),
    ...allergies.map((item, index) =>
      field(
        t("allergies"),
        item.text_original,
        `/allergies/${index}/text_original`,
        index + 130,
      ),
    ),
  ];

  return (
    <section className="doctor-consult">
      <header className="consult-header no-print">
        <div>
          <h2>{t("consultations")}</h2>
          <p>{t("consultVersion", { version: sheet.version })}</p>
        </div>
        <div className="consult-actions">
          {(visitStatus === "called" || visitStatus === null) && (
            <button type="button" onClick={() => void transition("start")}>
              {t("startConsult")}
            </button>
          )}
          {(visitStatus === "in_consult" || visitStatus === null) && (
            <button type="button" onClick={() => void transition("complete")}>
              {t("finishConsult")}
            </button>
          )}
          <button
            type="button"
            disabled={exportingPdf}
            onClick={() => void exportPdf()}
          >
            {exportingPdf ? t("exportingPdf") : t("exportPdf")}
          </button>
          <button type="button" onClick={() => window.print()}>
            {t("printCaseSheet")}
          </button>
          <Link to="/staff/queue">{t("backToQueue")}</Link>
        </div>
      </header>
      <p className="consult-shortcuts no-print">{t("shortcuts")}</p>
      {error && <p role="alert">{error}</p>}
      <div className="consult-columns">
        <article className="consult-sheet">
          <h3>{t("caseSheetTitle")}</h3>
          <dl>
            {summaryFields}
            {additionalFields}
          </dl>
          {redFlags.length > 0 && (
            <section className="consult-red-flags">
              <h4>{t("redFlags")}</h4>
              <ul>
                {redFlags.map((flag) => (
                  <li key={flag.id}>
                    <strong>{flag.level}</strong> ·{" "}
                    {flag.reason_en ?? flag.evidence}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <p className="consult-disclaimer">{data.disclaimer}</p>
        </article>
        <aside className="consult-transcript">
          <h3>{t("transcriptTitle")}</h3>
          {transcript.length === 0 ? (
            <p>{t("transcriptEmpty")}</p>
          ) : (
            <ol>
              {transcript.map((turn) => (
                <li key={turn.turn_index}>
                  <small>
                    {turn.question_id} · {turn.input_mode}
                  </small>
                  <p>{turn.text_original || turn.text_english || emptyText}</p>
                  {turn.text_original &&
                    turn.text_english &&
                    turn.text_original !== turn.text_english && (
                      <p className="consult-translation">{turn.text_english}</p>
                    )}
                </li>
              ))}
            </ol>
          )}
        </aside>
      </div>
      {conflict && (
        <div className="consult-dialog-backdrop">
          <section
            className="consult-conflict"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="conflict-title"
          >
            <h3 id="conflict-title">{t("editConflictTitle")}</h3>
            <p>{t("editConflictMessage")}</p>
            <button type="button" onClick={() => void load()}>
              {t("reloadCaseSheet")}
            </button>
            <button
              type="button"
              onClick={async () => {
                const current = await load();
                if (current) await saveEdit(current.version);
              }}
            >
              {t("reapplyEdit")}
            </button>
            <button
              type="button"
              onClick={() => {
                setConflict(false);
                setDraft(undefined);
              }}
            >
              {t("discardEdit")}
            </button>
          </section>
        </div>
      )}
    </section>
  );
}
