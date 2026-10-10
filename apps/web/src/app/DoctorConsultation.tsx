import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  Download,
  FileText,
  Hand,
  Megaphone,
  MessageSquareText,
  Mic,
  Pencil,
  Play,
  Printer,
  Sparkles,
  Stethoscope,
  UserRound,
} from "lucide-react";
import type { components } from "../lib/api-types";
import { staffAuth } from "../lib/auth";
import { ConfidenceChip } from "../components/ui";
import { ConsultPatientRail } from "./ConsultPatientRail";
import { DANGER_TILE_LABELS } from "../lib/dangerTiles";
import type { Triage } from "./queueBoardModel";
import { TriageWhy } from "./TriageWhy";

type CaseSheet = components["schemas"]["CaseSheet"] & {
  ai_summary?: {
    brief_en: string;
    answers_en?: Record<string, string>;
    model?: string;
    generated_at?: string;
  } | null;
  followup_questions?: Record<string, string>;
};
type TextPair = {
  text_original: string;
  text_en: string;
  confidence?: number | null;
};
type PatientContext = {
  age_years?: number | null;
  sex?: string | null;
  language?: string | null;
  token?: string | null;
  urgency?: string | null;
  status?: string | null;
  department_name?: string | null;
  triage?: Triage | null;
  queued_at?: string | null;
};
type TranscriptTurn = {
  turn_index: number;
  question_id: string;
  input_mode: string;
  text_original: string | null;
  text_english: string | null;
  stt_confidence: number | null;
};
type CaseSheetResult = {
  version: number;
  case_sheet: CaseSheet;
  confirmed_by_patient: boolean;
  confirmed_at: string | null;
  patient?: PatientContext | null;
};

const emptyText = "—";

export function DoctorConsultation() {
  const { t, i18n } = useTranslation("staff");
  const [searchParams, setSearchParams] = useSearchParams();
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
      setSheet((previous) => ({
        ...updated,
        patient: updated.patient ?? previous?.patient,
        version: Number.isFinite(headerVersion)
          ? headerVersion
          : updated.version,
      }));
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

  async function transition(action: "call" | "start" | "complete") {
    if (!visitId) return;
    try {
      const response = await staffAuth.request(`/queue/${visitId}/${action}`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      if (!response.ok) throw new Error(t("consultActionError"));
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set(
            "status",
            action === "call" ? "called" : action === "start" ? "in_consult" : "done",
          );
          return next;
        },
        { replace: true },
      );
      setActionState({
        visitId,
        status:
          action === "call" ? "called" : action === "start" ? "in_consult" : "done",
      });
      if (action === "complete") navigate("/staff/consultations");
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
    original?: string | null,
    metaPath?: string,
    repeat = false,
  ) {
    const text =
      value === null || value === undefined || value === ""
        ? emptyText
        : String(value);
    const fieldMeta = sheet?.case_sheet.field_meta;
    const metadata =
      fieldMeta?.[metaPath ?? path] ??
      fieldMeta?.[`${metaPath ?? path}/text_original`] ??
      fieldMeta?.[path];
    const SourceIcon = metadata
      ? {
          patient_voice: Mic,
          patient_touch: Hand,
          patient_text: MessageSquareText,
          nurse: UserRound,
          doctor: Stethoscope,
          document: FileText,
        }[metadata.source]
      : undefined;
    const editing = draft?.path === path;
    return (
      <div className={`cx-field${repeat ? " cx-field--repeat" : ""}`} key={path}>
        <dt className={repeat ? "cx-visually-hidden" : undefined}>{label}</dt>
        <dd>
          <div className="cx-field-main">
            <div className="cx-field-value">
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
            {!editing && editable && (
              <button
                ref={index === 0 ? firstEditRef : undefined}
                type="button"
                className="cx-edit"
                onClick={() =>
                  setDraft({ path, value: text === emptyText ? "" : text })
                }
              >
                <Pencil aria-hidden="true" size={13} />
                {t("editField")}
              </button>
            )}
          </div>
          {!editing && original && original !== text && (
            <div className="cx-original" lang={sheet?.case_sheet.language}>
              <span>{t("patientWords")}:</span> {original}
            </div>
          )}
          {metadata && SourceIcon && (
            <div className="cx-provenance">
              <span className="cx-source" title={t(`source_${metadata.source}`)}>
                <SourceIcon aria-hidden="true" size={13} />
                <span>{t(`source_${metadata.source}`)}</span>
              </span>
              {metadata.ai_assisted && <ConfidenceChip value={metadata.confidence} />}
              {metadata.ai_assisted && (
                <span className="cx-ai-chip">{t("aiAssisted")}</span>
              )}
            </div>
          )}
          {editing && (
            <div className="cx-field-actions">
              <button
                type="button"
                className="cx-btn cx-btn--primary"
                onClick={() => void saveEdit()}
                disabled={saving || !draft.value.trim()}
              >
                {t("saveEdit")}
              </button>
              <button type="button" className="cx-btn" onClick={() => setDraft(undefined)}>
                {t("cancelEdit")}
              </button>
            </div>
          )}
        </dd>
      </div>
    );
  }

  const rail = <ConsultPatientRail activeVisitId={visitId} />;

  if (!visitId) {
    return (
      <div className="cx">
        {rail}
        <section className="doctor-consult cx-main">
          <div className="cx-placeholder">
            <ClipboardList aria-hidden="true" size={40} />
            <h2>{t("consultations")}</h2>
            <p role="status">{t("chooseVisit")}</p>
            <ul>
              <li>{t("consultHintSheet")}</li>
              <li>{t("consultHintEdit")}</li>
              <li>{t("consultHintPdf")}</li>
            </ul>
            <Link className="cx-btn" to="/staff/queue">
              {t("backToQueue")}
            </Link>
          </div>
        </section>
      </div>
    );
  }
  if (loading)
    return (
      <div className="cx">
        {rail}
        <section className="doctor-consult cx-main">
          <p className="cx-placeholder" role="status">{t("loadingConsult")}</p>
        </section>
      </div>
    );
  if (error && !sheet)
    return (
      <div className="cx">
        {rail}
        <section className="doctor-consult cx-main">
          <div className="cx-placeholder">
            <p role="alert">{error}</p>
            <button type="button" className="cx-btn" onClick={() => void load()}>
              {t("retry")}
            </button>
          </div>
        </section>
      </div>
    );
  if (!sheet)
    return (
      <div className="cx">
        {rail}
        <section className="doctor-consult cx-main">
          <p className="cx-placeholder" role="status">{t("caseSheetUnavailable")}</p>
        </section>
      </div>
    );

  const data = sheet.case_sheet;
  const patient = sheet.patient;
  const hpi = data.hpi;
  const pair = (
    label: string,
    value: TextPair | null | undefined,
    path: string,
    index: number,
    repeat = false,
  ) =>
    value
      ? field(
          label,
          value.text_en,
          `${path}/text_en`,
          index,
          true,
          value.text_original,
          path,
          repeat,
        )
      : null;
  const pairs = (
    label: string,
    values: TextPair[] | undefined,
    path: string,
    offset: number,
  ) =>
    (values ?? []).map((item, index) =>
      pair(label, item, `${path}/${index}`, offset + index, index > 0),
    );
  const confirmedTime = sheet.confirmed_at
    ? new Intl.DateTimeFormat(i18n.language, {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Kolkata",
      }).format(new Date(sheet.confirmed_at))
    : "";
  const corrections = data.patient_confirmation?.corrections ?? [];
  const redFlags = data.red_flags ?? [];
  const presentFields = [
    pair(t("onsetDuration"), hpi.onset as TextPair | null, "/hpi/onset", 1),
    typeof hpi.severity_0_10 === "number"
      ? field(
          t("severity"),
          `${hpi.severity_0_10} / 10`,
          "/hpi/severity_0_10",
          2,
          false,
        )
      : null,
    pair(t("site"), hpi.location as TextPair | null, "/hpi/location", 3),
    pair(t("character"), hpi.character as TextPair | null, "/hpi/character", 4),
    pair(t("timing"), hpi.timing as TextPair | null, "/hpi/timing", 5),
    ...pairs(
      t("associatedSymptoms"),
      hpi.associated_symptoms as TextPair[],
      "/hpi/associated_symptoms",
      10,
    ),
    ...pairs(
      t("negatives"),
      hpi.pertinent_negatives as TextPair[],
      "/hpi/pertinent_negatives",
      40,
    ),
    ...pairs(t("aggravating"), hpi.aggravating as TextPair[], "/hpi/aggravating", 70),
    ...pairs(t("relieving"), hpi.relieving as TextPair[], "/hpi/relieving", 90),
  ].filter(Boolean);
  const pastFields = [
    ...(data.past_history?.conditions ?? []).map((item, index) =>
      field(
        t("pastHistory"),
        item,
        `/past_history/conditions/${index}`,
        110 + index,
        true,
        undefined,
        undefined,
        index > 0,
      ),
    ),
    ...(data.past_history?.surgeries ?? []).map((item, index) =>
      field(
        t("pastSurgery"),
        item,
        `/past_history/surgeries/${index}`,
        140 + index,
        true,
        undefined,
        undefined,
        index > 0,
      ),
    ),
    ...(data.medications ?? []).map((item, index) =>
      field(
        t("medications"),
        item.dose ? `${item.name} (${item.dose})` : item.name,
        `/medications/${index}/name`,
        170 + index,
        true,
        undefined,
        undefined,
        index > 0,
      ),
    ),
    ...pairs(t("allergies"), data.allergies as TextPair[], "/allergies", 200),
    ...pairs(t("familyHistory"), data.family_history as TextPair[], "/family_history", 230),
    pair(t("tobacco"), data.social?.tobacco as TextPair | null, "/social/tobacco", 260),
    pair(t("alcohol"), data.social?.alcohol as TextPair | null, "/social/alcohol", 261),
    data.women_health?.pregnant
      ? field(t("pregnancy"), t("pregnantNow"), "/women_health/pregnant", 262, false)
      : null,
  ].filter(Boolean);
  const urgency = patient?.urgency ?? "routine";
  const questionLabel = (id: string) => {
    const key = `question_${id}`;
    return i18n.exists(key, { ns: "staff" })
      ? t(key)
      : id.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
  };
  const ModeIcon = (mode: string) =>
    mode === "voice" ? Mic : mode === "touch" ? Hand : MessageSquareText;

  return (
    <div className="cx">
      {rail}
      <section className="doctor-consult cx-main">
        <header className={`cx-hero cx-hero--${urgency}`}>
          <div className="cx-hero-id">
            <span className="cx-token">{patient?.token ?? "—"}</span>
            <div>
              <h2>
                {patient && typeof patient.age_years === "number"
                  ? t("yearsShort", { age: patient.age_years })
                  : "—"}
                {" · "}
                {t(`sex_${patient?.sex ?? "unknown"}`)}
              </h2>
              <div className="cx-hero-meta">
                <span className={`cx-pill cx-pill--urgency-${urgency}`}>
                  {urgency !== "routine" && <AlertTriangle aria-hidden="true" size={13} />}
                  {t(`urgency_${urgency}`)}
                </span>
                {patient?.triage && (
                  <TriageWhy triage={patient.triage} queuedAt={patient.queued_at ?? null} />
                )}
                {patient?.department_name && <span>{patient.department_name}</span>}
                {patient?.language && (
                  <span>{t("answeredIn", { language: t(`lang_${patient.language}`) })}</span>
                )}
                <span>{t("consultVersion", { version: sheet.version })}</span>
              </div>
            </div>
          </div>
          <div className="cx-actions no-print">
            {visitStatus === "queued" && (
              <button
                type="button"
                className="cx-btn cx-btn--primary"
                onClick={() => void transition("call")}
              >
                <Megaphone aria-hidden="true" size={15} /> {t("callPatient")}
              </button>
            )}
            {(visitStatus === "called" || visitStatus === null) && (
              <button
                type="button"
                className="cx-btn cx-btn--primary"
                onClick={() => void transition("start")}
              >
                <Play aria-hidden="true" size={15} /> {t("startConsult")}
              </button>
            )}
            {(visitStatus === "in_consult" || visitStatus === null) && (
              <button
                type="button"
                className="cx-btn cx-btn--primary"
                onClick={() => void transition("complete")}
              >
                <CheckCircle2 aria-hidden="true" size={15} /> {t("finishConsult")}
              </button>
            )}
            <button
              type="button"
              className="cx-btn"
              disabled={exportingPdf}
              onClick={() => void exportPdf()}
            >
              <Download aria-hidden="true" size={15} />
              {exportingPdf ? t("exportingPdf") : t("exportPdf")}
            </button>
            <button type="button" className="cx-btn" onClick={() => window.print()}>
              <Printer aria-hidden="true" size={15} /> {t("printCaseSheet")}
            </button>
          </div>
        </header>

        <div className="cx-strip no-print">
          {sheet.confirmed_by_patient && confirmedTime ? (
            <span className="cx-strip-ok">
              <CheckCircle2 aria-hidden="true" size={15} />
              <span>{t("confirmedByPatientAt", { time: confirmedTime })}</span>
              {corrections.length > 0 && (
                <span className="cx-strip-note">
                  {t("correctionsRecorded", { corrections: corrections.join(", ") })}
                </span>
              )}
            </span>
          ) : (
            <span className="cx-strip-warn">{t("patientConfirmationPending")}</span>
          )}
          {data.patient_confirmation?.needs_assistance && (
            <span className="cx-strip-warn">
              <Hand aria-hidden="true" size={15} /> {t("patientNeedsAssistance")}
            </span>
          )}
          {Object.values(data.field_meta ?? {}).some((meta) => meta.ai_assisted) && (
            <span className="cx-strip-ai">
              <Sparkles aria-hidden="true" size={14} /> {t("aiAssistedNote")}
            </span>
          )}
          <span className="cx-shortcuts">{t("shortcuts")}</span>
        </div>
        {error && <p className="cx-error" role="alert">{error}</p>}

        <div className="cx-columns">
          <article className="cx-sheet">
            <h3 className="cx-visually-hidden">{t("caseSheetTitle")}</h3>
            {redFlags.length > 0 && (
              <section className="cx-card cx-card--danger">
                <h4>
                  <AlertTriangle aria-hidden="true" size={17} /> {t("redFlags")}
                </h4>
                <ul>
                  {redFlags.map((flag) => (
                    <li key={flag.id}>
                      <strong>{t(`urgency_${flag.level}`)}</strong> ·{" "}
                      {flag.reason_en ?? flag.evidence}
                      {flag.reason_en && flag.evidence && DANGER_TILE_LABELS[flag.evidence] ? (
                        <span className="cx-flag-evidence">
                          Selected on the safety screen: {DANGER_TILE_LABELS[flag.evidence]}
                        </span>
                      ) : (
                        flag.reason_en &&
                        flag.evidence &&
                        !flag.evidence.startsWith("Structured intake answers") && (
                          <span className="cx-flag-evidence" lang={data.language}>
                            {t("patientWords")}: “{flag.evidence}”
                          </span>
                        )
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {data.ai_summary?.brief_en && (
              <section className="cx-card cx-card--ai">
                <h4>
                  <Sparkles aria-hidden="true" size={17} /> {t("aiBriefTitle")}
                </h4>
                <p>{data.ai_summary.brief_en}</p>
                <small>{t("aiBriefNote")}</small>
              </section>
            )}
            <section className="cx-card">
              <h4>{t("presentingComplaint")}</h4>
              <dl>
                {field(
                  t("chiefComplaint"),
                  data.chief_complaint.text_en,
                  "/chief_complaint/text_en",
                  0,
                  true,
                  data.chief_complaint.text_original,
                  "/chief_complaint",
                )}
              </dl>
            </section>
            <section className="cx-card">
              <h4>{t("hpiTitle")}</h4>
              {presentFields.length ? (
                <dl>{presentFields}</dl>
              ) : (
                <p className="cx-none">{t("noneRecorded")}</p>
              )}
            </section>
            <section className="cx-card">
              <h4>{t("pastTitle")}</h4>
              {pastFields.length ? (
                <dl>{pastFields}</dl>
              ) : (
                <p className="cx-none">{t("noneRecorded")}</p>
              )}
            </section>
            <p className="cx-disclaimer">{data.disclaimer}</p>
          </article>

          <aside className="cx-transcript">
            <h3>{t("transcriptTitle")}</h3>
            {transcript.length === 0 ? (
              <p className="cx-none">{t("transcriptEmpty")}</p>
            ) : (
              <ol>
                {transcript.map((turn) => {
                  const english =
                    data.ai_summary?.answers_en?.[turn.question_id] ??
                    turn.text_english;
                  const aiQuestion = data.followup_questions?.[turn.question_id];
                  const Icon = ModeIcon(turn.input_mode);
                  return (
                    <li key={turn.turn_index}>
                      <div className="cx-turn-head">
                        {aiQuestion ? (
                          <span className="cx-turn-q">
                            <span className="cx-ai-chip">
                              <Sparkles aria-hidden="true" size={11} /> {t("aiFollowup")}
                            </span>
                            {aiQuestion}
                          </span>
                        ) : (
                          <span className="cx-turn-q">{questionLabel(turn.question_id)}</span>
                        )}
                        <span className="cx-mode" title={t(`inputMode_${turn.input_mode}`, turn.input_mode)}>
                          <Icon aria-hidden="true" size={12} />
                          {t(`inputMode_${turn.input_mode}`, turn.input_mode)}
                        </span>
                      </div>
                      <p className="cx-turn-answer" lang={data.language}>
                        {turn.text_original || english || emptyText}
                      </p>
                      {turn.text_original &&
                        english &&
                        turn.text_original !== english && (
                          <p className="cx-translation">{english}</p>
                        )}
                    </li>
                  );
                })}
              </ol>
            )}
          </aside>
        </div>
      </section>
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
    </div>
  );
}

