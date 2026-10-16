import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { flushSync } from "react-dom";
import { Link, useBlocker, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ClipboardList,
  Clock3,
  Download,
  FileText,
  Hand,
  History,
  Info,
  Keyboard,
  Languages,
  Megaphone,
  MessageSquareText,
  Mic,
  MoreHorizontal,
  Pencil,
  Play,
  Printer,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Undo2,
  UserRound,
  UserX,
} from "lucide-react";
import type { components } from "../lib/api-types";
import { staffAuth } from "../lib/auth";
import { ConfidenceChip, Popover } from "../components/ui";
import { ConsultPatientRailView } from "./ConsultPatientRail";
import { PreviousVisits, type PatientHistory } from "./PreviousVisits";
import { useConsultQueue } from "./consultQueue";
import { DANGER_TILE_LABELS } from "../lib/dangerTiles";
import { newRequestId } from "../lib/ids";
import { formatCountdown, graceLeft, type Triage } from "./queueBoardModel";
import { queueProblem } from "./queueProblem";
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
type Draft = { path: string; value: string; initial: string; label: string };
type Tab = "overview" | "history" | "previous" | "transcript" | "notes";
type StepState = "done" | "active" | "next" | "upcoming";

const emptyText = "—";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "tabOverview" },
  { id: "history", label: "tabHistory" },
  // Earlier visits stay apart from today's history so the two are never confused.
  { id: "previous", label: "tabPrevious" },
  { id: "transcript", label: "tabTranscript" },
  { id: "notes", label: "tabNotes" },
];
const STAFF_SOURCES = new Set(["doctor", "nurse"]);
/** Case-sheet paths to field labels, for the list of staff edits. */
const PATH_LABELS: [RegExp, string][] = [
  [/^\/chief_complaint/, "chiefComplaint"],
  [/^\/hpi\/onset/, "onsetDuration"],
  [/^\/hpi\/severity/, "severity"],
  [/^\/hpi\/location/, "site"],
  [/^\/hpi\/character/, "character"],
  [/^\/hpi\/timing/, "timing"],
  [/^\/hpi\/associated_symptoms/, "associatedSymptoms"],
  [/^\/hpi\/pertinent_negatives/, "negatives"],
  [/^\/hpi\/aggravating/, "aggravating"],
  [/^\/hpi\/relieving/, "relieving"],
  [/^\/past_history\/conditions/, "pastHistory"],
  [/^\/past_history\/surgeries/, "pastSurgery"],
  [/^\/medications/, "medications"],
  [/^\/allergies/, "allergies"],
  [/^\/family_history/, "familyHistory"],
  [/^\/social\/tobacco/, "tobacco"],
  [/^\/social\/alcohol/, "alcohol"],
];
/** Where each visit status sits on the call → consult → complete path. */
const WORKFLOW: Record<string, StepState[]> = {
  queued: ["next", "upcoming", "upcoming"],
  called: ["done", "next", "upcoming"],
  in_consult: ["done", "active", "next"],
  // Missed the call: recalled to the queue, then called again.
  skipped: ["next", "upcoming", "upcoming"],
  done: ["done", "done", "done"],
};
type QueueAction = "call" | "start" | "complete" | "skip" | "recall" | "return";
const NEXT_STATUS: Record<QueueAction, string> = {
  call: "called",
  start: "in_consult",
  complete: "done",
  skip: "skipped",
  recall: "queued",
  return: "queued",
};
type QueueEvent = {
  from: string;
  to: string;
  actor_type: "user" | "patient" | "system";
  actor_name: string | null;
  actor_role: string | null;
  reason: string | null;
  at: string;
};

const visitParam = (search: string) => new URLSearchParams(search).get("visit_id");

export function DoctorConsultation() {
  const { t, i18n } = useTranslation("staff");
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const visitId = searchParams.get("visit_id");
  const routeStatus = searchParams.get("status");
  const queue = useConsultQueue(visitId);
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
  const [draft, setDraft] = useState<Draft>();
  const [saving, setSaving] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [printing, setPrinting] = useState(false);
  // The section each patient was left on; a patient not opened yet starts on Overview.
  const [tabs, setTabs] = useState<Record<string, Tab>>({});
  const tab: Tab = (visitId && tabs[visitId]) || "overview";
  const firstEditRef = useRef<HTMLButtonElement>(null);
  const focusEditPending = useRef(false);
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  const baseId = useId();

  // An edit belongs to one patient: never carry it over to the next one.
  const [draftVisit, setDraftVisit] = useState(visitId);
  if (draftVisit !== visitId) {
    setDraftVisit(visitId);
    setDraft(undefined);
    setConflict(false);
  }
  const dirty = Boolean(draft && draft.value !== draft.initial);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      (currentLocation.pathname !== nextLocation.pathname ||
        visitParam(currentLocation.search) !== visitParam(nextLocation.search)),
  );

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const openTab = useCallback(
    (next: Tab) => {
      if (!visitId) return;
      setTabs((current) => ({ ...current, [visitId]: next }));
    },
    [visitId],
  );

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

  // Earlier visits load on their own: a failure there never blocks today's case sheet.
  const [history, setHistory] = useState<{ visitId: string; data: PatientHistory }>();
  const [historyFailed, setHistoryFailed] = useState<"error" | "closed" | null>(null);
  const loadHistory = useCallback(async () => {
    if (!visitId) return;
    try {
      const response = await staffAuth.request(`/visits/${visitId}/history`);
      if (response.status === 403) {
        setHistoryFailed("closed");
        return;
      }
      if (!response.ok) throw new Error("history unavailable");
      const data = (await response.json()) as PatientHistory;
      if (!Array.isArray(data.previous_visits)) throw new Error("unexpected history");
      setHistory({ visitId, data });
      setHistoryFailed(null);
    } catch {
      setHistoryFailed("error");
    }
  }, [visitId]);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadHistory(), 0);
    return () => window.clearTimeout(timer);
  }, [loadHistory]);
  const patientHistory = history?.visitId === visitId ? history.data : undefined;

  // The visit's queue timeline (token, calls, missed calls, recalls); extra information only.
  const [timeline, setTimeline] = useState<{ visitId: string; events: QueueEvent[] }>();
  const loadTimeline = useCallback(async () => {
    if (!visitId) return;
    try {
      const response = await staffAuth.request(`/visits/${visitId}/queue-events`);
      if (!response.ok) return;
      const body = (await response.json()) as { events?: QueueEvent[] };
      if (Array.isArray(body.events)) setTimeline({ visitId, events: body.events });
    } catch {
      // The consultation works without the timeline.
    }
  }, [visitId]);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadTimeline(), 0);
    return () => window.clearTimeout(timer);
  }, [loadTimeline]);
  const visitTimeline = timeline?.visitId === visitId ? timeline.events : [];

  // A called patient can be marked absent once the hospital's grace period has passed.
  const calledAt = queue.items.find((item) => item.visit_id === visitId)?.called_at;
  const [tick, setTick] = useState(() => Date.now());
  const absentWait =
    visitStatus === "called" ? graceLeft(calledAt, queue.graceSeconds ?? 180, tick) : 0;
  const countingDown = absentWait > 0;
  useEffect(() => {
    if (!countingDown) return;
    const timer = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [countingDown]);

  // Printing always produces the full case sheet, whichever section is on screen.
  useEffect(() => {
    const before = () => flushSync(() => setPrinting(true));
    const after = () => setPrinting(false);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);
  const printSheet = useCallback(() => {
    flushSync(() => setPrinting(true));
    window.print();
  }, []);

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
      const key = event.key.toLowerCase();
      if (key === "n") navigate("/staff/queue");
      if (key === "e") {
        if (firstEditRef.current) firstEditRef.current.focus();
        else {
          focusEditPending.current = true;
          openTab("history");
        }
      }
      if (key === "p") printSheet();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate, openTab, printSheet]);

  // "E" from another section opens Patient history; focus its first edit once it is drawn.
  useEffect(() => {
    if (focusEditPending.current && firstEditRef.current) {
      focusEditPending.current = false;
      firstEditRef.current.focus();
    }
  });

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

  async function transition(action: QueueAction) {
    if (!visitId) return;
    const status = NEXT_STATUS[action];
    try {
      const response = await staffAuth.request(`/queue/${visitId}/${action}`, {
        method: "POST",
        headers: { "Idempotency-Key": newRequestId() },
      });
      if (!response.ok)
        throw new Error((await queueProblem(response, t)) ?? t("consultActionError"));
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("status", status);
          return next;
        },
        { replace: true },
      );
      setActionState({ visitId, status });
      setError("");
      queue.reload();
      void loadTimeline();
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
                    setDraft({ ...draft, value: event.target.value })
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
                onClick={() => {
                  const initial = text === emptyText ? "" : text;
                  setDraft({ path, value: initial, initial, label });
                }}
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
              {STAFF_SOURCES.has(metadata.source) && (
                <span className="cx-verified-chip">
                  <ShieldCheck aria-hidden="true" size={12} />
                  {t("staffVerified")}
                </span>
              )}
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

  const rail = <ConsultPatientRailView activeVisitId={visitId} queue={queue} />;
  const leaveDialog =
    blocker.state === "blocked" ? (
      <div className="consult-dialog-backdrop">
        <section
          className="consult-conflict"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby={`${baseId}-leave`}
        >
          <h3 id={`${baseId}-leave`}>{t("leaveTitle")}</h3>
          <p>{t("leaveMessage", { field: draft?.label ?? "" })}</p>
          <button type="button" onClick={() => blocker.reset()}>
            {t("keepEditing")}
          </button>
          <button
            type="button"
            onClick={() => {
              setDraft(undefined);
              blocker.proceed();
            }}
          >
            {t("discardAndLeave")}
          </button>
        </section>
      </div>
    ) : null;
  const shell = (content: ReactNode) => (
    <div className="cx">
      <div className="cx-layout">
        {rail}
        <section className="doctor-consult cx-main">{content}</section>
      </div>
      {leaveDialog}
    </div>
  );

  if (!visitId) {
    const noPatients = queue.state === "ready" && queue.items.length === 0;
    return shell(
      <div className="cx-empty">
        <span className="cx-empty-icon">
          <ClipboardList aria-hidden="true" size={28} />
        </span>
        <h2>{noPatients ? t("noPatientsTitle") : t("selectPatientTitle")}</h2>
        <p role="status">{noPatients ? t("noPatientsText") : t("chooseVisit")}</p>
        <ul className="cx-empty-hints">
          <li>
            <FileText aria-hidden="true" size={16} />
            {t("consultHintSheet")}
          </li>
          <li>
            <Pencil aria-hidden="true" size={16} />
            {t("consultHintEdit")}
          </li>
          <li>
            <Download aria-hidden="true" size={16} />
            {t("consultHintPdf")}
          </li>
        </ul>
        <div className="cx-empty-actions">
          {noPatients && (
            <button type="button" className="cx-btn" onClick={queue.reload}>
              <RefreshCw aria-hidden="true" size={15} />
              {t("refreshList")}
            </button>
          )}
          <Link className="cx-btn" to="/staff/queue">
            {t("backToQueue")}
          </Link>
        </div>
      </div>,
    );
  }
  if (loading)
    return shell(
      <p className="cx-placeholder" role="status">
        {t("loadingConsult")}
      </p>,
    );
  if (error && !sheet)
    return shell(
      <div className="cx-placeholder">
        <p role="alert">{error}</p>
        <button type="button" className="cx-btn" onClick={() => void load()}>
          {t("retry")}
        </button>
      </div>,
    );
  if (!sheet)
    return shell(
      <p className="cx-placeholder" role="status">
        {t("caseSheetUnavailable")}
      </p>,
    );

  const data = sheet.case_sheet;
  const patient = sheet.patient;
  const hpi = data.hpi;
  const fieldMeta = data.field_meta ?? {};
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
  const aiFieldsPending = Object.values(fieldMeta).some(
    (meta) => meta.ai_assisted && !STAFF_SOURCES.has(meta.source),
  );
  const ccMeta = fieldMeta["/chief_complaint/text_en"] ?? fieldMeta["/chief_complaint"];
  const textList = (values: unknown) =>
    ((values as TextPair[] | undefined) ?? []).map((item) => item.text_en).filter(Boolean);

  // ---- Overview pieces -------------------------------------------------------------------
  const redFlagCard = redFlags.length > 0 && (
    <section className="cx-card cx-card--danger" aria-labelledby={`${baseId}-flags`}>
      <h4 id={`${baseId}-flags`}>
        <AlertTriangle aria-hidden="true" size={17} /> {t("redFlagsReview")}
      </h4>
      <ul className="cx-flags">
        {redFlags.map((flag) => (
          <li key={flag.id}>
            <span className={`cx-flag-level cx-flag-level--${flag.level}`}>
              {t(`urgency_${flag.level}`)}
            </span>
            <div className="cx-flag-body">
              <span className="cx-flag-reason">{flag.reason_en ?? flag.evidence}</span>
              {flag.reason_en && flag.evidence && DANGER_TILE_LABELS[flag.evidence] ? (
                <span className="cx-flag-evidence">
                  {t("safetyScreenSelected", { label: DANGER_TILE_LABELS[flag.evidence] })}
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
              <span className="cx-flag-source">{t(`flagSource_${flag.source}`)}</span>
            </div>
          </li>
        ))}
      </ul>
      <div className="cx-card-foot no-print">
        <p>{t("redFlagsExplain")}</p>
        <button type="button" className="cx-link" onClick={() => openTab("transcript")}>
          {t("reviewResponses")} <ArrowRight aria-hidden="true" size={14} />
        </button>
      </div>
    </section>
  );
  const aiCard = data.ai_summary?.brief_en && (
    <section className="cx-card cx-card--ai" aria-labelledby={`${baseId}-ai`}>
      <h4 id={`${baseId}-ai`}>
        <Sparkles aria-hidden="true" size={17} /> {t("aiBriefTitle")}
      </h4>
      <p>{data.ai_summary.brief_en}</p>
      <div className="cx-card-foot">
        <small>{t("aiBriefNote")}</small>
        <span className="cx-verify">
          <ShieldAlert aria-hidden="true" size={14} />
          {t("aiNotVerified")}
        </span>
        <button
          type="button"
          className="cx-link no-print"
          onClick={() => openTab("transcript")}
        >
          {t("compareOriginal")} <ArrowRight aria-hidden="true" size={14} />
        </button>
      </div>
    </section>
  );
  const severity = typeof hpi.severity_0_10 === "number" ? hpi.severity_0_10 : null;
  const complaintRows: [string, ReactNode][] = [
    [
      t("chiefComplaint"),
      <>
        {data.chief_complaint.text_en || emptyText}
        <span
          className={`cx-origin${ccMeta && STAFF_SOURCES.has(ccMeta.source) ? " is-verified" : ""}`}
        >
          {ccMeta && STAFF_SOURCES.has(ccMeta.source) ? t("staffVerified") : t("patientReported")}
        </span>
      </>,
    ],
  ];
  if (hpi.onset) complaintRows.push([t("onsetDuration"), (hpi.onset as TextPair).text_en]);
  if (severity !== null)
    complaintRows.push([
      t("severityReported"),
      <span className={`cx-sev${severity >= 7 ? " is-high" : ""}`}>{severity} / 10</span>,
    ]);
  if (hpi.location) complaintRows.push([t("site"), (hpi.location as TextPair).text_en]);
  const associated = textList(hpi.associated_symptoms);
  if (associated.length) complaintRows.push([t("associatedSymptoms"), associated.join(", ")]);
  const negatives = textList(hpi.pertinent_negatives);
  if (negatives.length) complaintRows.push([t("negatives"), negatives.join(", ")]);
  const complaintCard = (
    <section className="cx-card" aria-labelledby={`${baseId}-complaint`}>
      <div className="cx-card-head">
        <h4 id={`${baseId}-complaint`}>{t("presentingComplaint")}</h4>
        <button type="button" className="cx-link" onClick={() => openTab("history")}>
          <Pencil aria-hidden="true" size={13} /> {t("editResponses")}
        </button>
      </div>
      <dl className="cx-kv">
        {complaintRows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
  const historyPoints: { label: string; text: string }[] = [
    ...(data.past_history?.conditions ?? []).map((text) => ({ label: t("pastHistory"), text })),
    ...(data.past_history?.surgeries ?? []).map((text) => ({ label: t("pastSurgery"), text })),
    ...(data.medications ?? []).map((item) => ({
      label: t("medications"),
      text: item.dose ? `${item.name} (${item.dose})` : item.name,
    })),
    ...textList(data.allergies).map((text) => ({ label: t("allergies"), text })),
    ...textList(data.family_history).map((text) => ({ label: t("familyHistory"), text })),
    ...textList([data.social?.tobacco].filter(Boolean)).map((text) => ({
      label: t("tobacco"),
      text,
    })),
    ...textList([data.social?.alcohol].filter(Boolean)).map((text) => ({
      label: t("alcohol"),
      text,
    })),
    ...(data.women_health?.pregnant ? [{ label: t("pregnancy"), text: t("pregnantNow") }] : []),
  ];
  const keyHistoryCard = (
    <section className="cx-card" aria-labelledby={`${baseId}-keyhistory`}>
      <div className="cx-card-head">
        <h4 id={`${baseId}-keyhistory`}>{t("keyHistory")}</h4>
        <button type="button" className="cx-link" onClick={() => openTab("history")}>
          {t("seeFullHistory")} <ArrowRight aria-hidden="true" size={14} />
        </button>
      </div>
      {historyPoints.length ? (
        <ul className="cx-points">
          {historyPoints.slice(0, 5).map((point, index) => (
            <li key={`${point.label}-${index}`}>
              <span>{point.label}</span>
              {point.text}
            </li>
          ))}
        </ul>
      ) : (
        <p className="cx-none">{t("noPastHistory")}</p>
      )}
      {historyPoints.length > 5 && (
        <p className="cx-more-note">{t("moreHistoryItems", { count: historyPoints.length - 5 })}</p>
      )}
    </section>
  );
  const timelineTime = new Intl.DateTimeFormat(i18n.language, {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  });
  const timelineLabel = (event: QueueEvent) => {
    if (event.to === "queued") {
      if (event.from === "skipped") return t("timelineRecalled");
      return event.from === "called" ? t("timelineReturned") : t("timelineTokenIssued");
    }
    const labels: Record<string, string> = {
      called: t("timelineCalled"),
      in_consult: t("timelineStarted"),
      skipped: t("timelineSkipped"),
      done: t("timelineDone"),
      no_show: t("timelineNoShow"),
      cancelled: t("timelineCancelled"),
    };
    return labels[event.to] ?? event.to;
  };
  const timelineActor = (event: QueueEvent) => {
    if (event.actor_type === "patient") return t("timelineByPatient");
    if (event.actor_type === "system") return t("timelineBySystem");
    return event.actor_name ? t("timelineBy", { name: event.actor_name }) : "";
  };
  const stepStates = visitStatus ? WORKFLOW[visitStatus] : undefined;
  const steps = [
    { title: t("workflowCall"), hint: t("workflowCallHint") },
    { title: t("workflowConsult"), hint: t("workflowConsultHint") },
    { title: t("workflowComplete"), hint: t("workflowCompleteHint") },
  ];
  const stepLabel: Record<StepState, string> = {
    done: t("stepDone"),
    active: t("stepActive"),
    next: t("stepNext"),
    upcoming: t("stepUpcoming"),
  };
  const workflowCard = (
    <section className="cx-card" aria-labelledby={`${baseId}-workflow`}>
      <h4 id={`${baseId}-workflow`}>{t("workflowTitle")}</h4>
      <ol className="cx-steps">
        {steps.map((step, index) => {
          const state = stepStates?.[index];
          return (
            <li key={step.title} className={state ? `is-${state}` : undefined}>
              <span className="cx-step-num" aria-hidden="true">
                {state === "done" ? <CheckCircle2 size={16} /> : index + 1}
              </span>
              <span className="cx-step-text">
                <strong>{step.title}</strong>
                <small>{step.hint}</small>
              </span>
              {state && <span className={`cx-step-state cx-step-state--${state}`}>{stepLabel[state]}</span>}
            </li>
          );
        })}
      </ol>
      {!stepStates && <p className="cx-none">{t("workflowUnknown")}</p>}
      {visitStatus === "skipped" && <p className="cx-panel-note">{t("consultSkippedNote")}</p>}
      {visitTimeline.length > 0 && (
        <div className="cx-timeline">
          <h5>
            <History aria-hidden="true" size={14} /> {t("timelineTitle")}
          </h5>
          <ol>
            {visitTimeline.map((event, index) => (
              <li key={`${event.at}-${index}`} className={`cx-timeline-item cx-timeline-item--${event.to}`}>
                <time dateTime={event.at}>{timelineTime.format(new Date(event.at))}</time>
                <span>
                  {timelineLabel(event)} <small>{timelineActor(event)}</small>
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
      <div className="cx-card-actions">
        <button type="button" className="cx-btn" onClick={() => openTab("transcript")}>
          <MessageSquareText aria-hidden="true" size={15} /> {t("viewTranscript")}
        </button>
        <button type="button" className="cx-btn" onClick={() => openTab("notes")}>
          <ClipboardList aria-hidden="true" size={15} /> {t("tabNotes")}
        </button>
      </div>
    </section>
  );

  // ---- Sections --------------------------------------------------------------------------
  const historyCards = (
    <>
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
    </>
  );
  const staffEdits = Array.from(
    new Map(
      Object.entries(fieldMeta)
        .filter(([, meta]) => STAFF_SOURCES.has(meta.source))
        .map(([path, meta]) => {
          const key = PATH_LABELS.find(([pattern]) => pattern.test(path))?.[1];
          const label = key ? t(key) : path;
          return [label, meta.source] as const;
        }),
    ),
  );

  let panel: ReactNode;
  if (tab === "overview") {
    panel = (
      <div className="cx-overview">
        <div className="cx-col">
          {redFlagCard}
          {complaintCard}
          {keyHistoryCard}
        </div>
        <div className="cx-col">
          {aiCard}
          {workflowCard}
        </div>
      </div>
    );
  } else if (tab === "history") {
    panel = (
      <article className="cx-sheet">
        <h3 className="cx-visually-hidden">{t("caseSheetTitle")}</h3>
        <p className="cx-panel-note">
          <Info aria-hidden="true" size={15} />
          {t("historyLegend")}
        </p>
        {historyCards}
        <p className="cx-disclaimer">{data.disclaimer}</p>
      </article>
    );
  } else if (tab === "previous") {
    panel = (
      <PreviousVisits
        visitId={visitId}
        history={patientHistory}
        failed={historyFailed}
        onLinked={() => void loadHistory()}
      />
    );
  } else if (tab === "transcript") {
    panel = (
      <section className="cx-transcript" aria-labelledby={`${baseId}-transcript`}>
        <div className="cx-card-head">
          <h3 id={`${baseId}-transcript`}>{t("transcriptTitle")}</h3>
          <button type="button" className="cx-link" onClick={() => openTab("history")}>
            <Pencil aria-hidden="true" size={13} /> {t("editInHistory")}
          </button>
        </div>
        <p className="cx-panel-note">
          <Info aria-hidden="true" size={15} />
          {t("transcriptNote")}
        </p>
        {transcript.length === 0 ? (
          <p className="cx-none">{t("transcriptEmpty")}</p>
        ) : (
          <ol>
            {transcript.map((turn) => {
              const aiEnglish = data.ai_summary?.answers_en?.[turn.question_id];
              const english = aiEnglish ?? turn.text_english;
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
                    <span className="cx-turn-tags">
                      <span className="cx-mode" title={t(`inputMode_${turn.input_mode}`, turn.input_mode)}>
                        <Icon aria-hidden="true" size={12} />
                        {t(`inputMode_${turn.input_mode}`, turn.input_mode)}
                      </span>
                      {turn.input_mode === "voice" && typeof turn.stt_confidence === "number" && (
                        <ConfidenceChip value={turn.stt_confidence} />
                      )}
                    </span>
                  </div>
                  <p className="cx-turn-answer" lang={data.language}>
                    {turn.text_original || english || emptyText}
                  </p>
                  {turn.text_original && english && turn.text_original !== english && (
                    <p className="cx-translation">
                      <span>{aiEnglish ? t("englishAiAssisted") : t("englishTranslation")}:</span>{" "}
                      {english}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </section>
    );
  } else {
    panel = (
      <div className="cx-notes">
        <section className="cx-card cx-card--info" aria-labelledby={`${baseId}-notes`}>
          <h4 id={`${baseId}-notes`}>
            <Info aria-hidden="true" size={16} /> {t("notesTitle")}
          </h4>
          <p>{t("notesUnsupported")}</p>
          <div className="cx-card-actions">
            <button type="button" className="cx-btn" onClick={() => openTab("history")}>
              <Pencil aria-hidden="true" size={15} /> {t("editResponses")}
            </button>
          </div>
        </section>
        <section className="cx-card" aria-labelledby={`${baseId}-edits`}>
          <h4 id={`${baseId}-edits`}>{t("staffEditsTitle")}</h4>
          {staffEdits.length ? (
            <ul className="cx-points">
              {staffEdits.map(([label, source]) => (
                <li key={label}>
                  <span>{label}</span>
                  {t(`source_${source}`)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="cx-none">{t("staffEditsNone")}</p>
          )}
          <p className="cx-audit">
            <History aria-hidden="true" size={14} />
            {t("versionAudit", { version: sheet.version })}
          </p>
        </section>
        {corrections.length > 0 && (
          <section className="cx-card" aria-labelledby={`${baseId}-corrections`}>
            <h4 id={`${baseId}-corrections`}>{t("patientCorrectionsTitle")}</h4>
            <ul className="cx-points">
              {corrections.map((correction) => (
                <li key={correction}>{correction}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    );
  }

  function onTabKey(event: ReactKeyboardEvent<HTMLButtonElement>) {
    const index = TABS.findIndex((item) => item.id === tab);
    const target =
      event.key === "ArrowRight"
        ? TABS[(index + 1) % TABS.length]
        : event.key === "ArrowLeft"
          ? TABS[(index - 1 + TABS.length) % TABS.length]
          : event.key === "Home"
            ? TABS[0]
            : event.key === "End"
              ? TABS[TABS.length - 1]
              : undefined;
    if (!target) return;
    event.preventDefault();
    openTab(target.id);
    tabRefs.current[target.id]?.focus();
  }

  const primary =
    visitStatus === "queued" ? (
      <button type="button" className="cx-btn cx-btn--primary" onClick={() => void transition("call")}>
        <Megaphone aria-hidden="true" size={15} /> {t("callPatient")}
      </button>
    ) : visitStatus === "skipped" ? (
      <button
        type="button"
        className="cx-btn cx-btn--primary"
        title={t("recallHint")}
        onClick={() => void transition("recall")}
      >
        <RotateCcw aria-hidden="true" size={15} /> {t("recall")}
      </button>
    ) : visitStatus === "called" || visitStatus === null ? (
      <>
        {visitStatus === "called" && (
          <button
            type="button"
            className="cx-btn"
            disabled={absentWait > 0}
            title={absentWait > 0 ? t("markAbsentHint") : undefined}
            onClick={() => void transition("skip")}
          >
            <UserX aria-hidden="true" size={15} />
            {absentWait > 0
              ? t("markAbsentIn", { time: formatCountdown(absentWait) })
              : t("markAbsent")}
          </button>
        )}
        <button type="button" className="cx-btn cx-btn--primary" onClick={() => void transition("start")}>
          <Play aria-hidden="true" size={15} /> {t("startConsult")}
        </button>
      </>
    ) : visitStatus === "in_consult" ? (
      <button type="button" className="cx-btn cx-btn--primary" onClick={() => void transition("complete")}>
        <CheckCircle2 aria-hidden="true" size={15} /> {t("finishConsult")}
      </button>
    ) : null;
  const statusLabel =
    visitStatus === "done"
      ? t("tileDone")
      : visitStatus && WORKFLOW[visitStatus]
        ? t(`queueStatus_${visitStatus}`)
        : null;

  return shell(
    <>
      <header className={`cx-head cx-head--${urgency}`}>
        <div className="cx-head-id">
          <span className="cx-token">{patient?.token ?? "—"}</span>
          <div className="cx-head-text">
            <h2>
              {patient && typeof patient.age_years === "number"
                ? t("yearsShort", { age: patient.age_years })
                : "—"}
              {" · "}
              {t(`sex_${patient?.sex ?? "unknown"}`)}
            </h2>
            <div className="cx-head-meta">
              {patient?.department_name && (
                <span>
                  <Stethoscope aria-hidden="true" size={14} />
                  {patient.department_name}
                </span>
              )}
              {patient?.language && (
                <span>
                  <Languages aria-hidden="true" size={14} />
                  {t("answeredIn", { language: t(`lang_${patient.language}`) })}
                </span>
              )}
              <span>
                <FileText aria-hidden="true" size={14} />
                {t("consultVersion", { version: sheet.version })}
              </span>
            </div>
            <div className="cx-head-badges">
              {statusLabel && (
                <span className={`cx-pill cx-pill--${visitStatus}`}>{statusLabel}</span>
              )}
              <span className={`cx-pill cx-pill--urgency-${urgency}`}>
                {urgency !== "routine" && <AlertTriangle aria-hidden="true" size={13} />}
                {t(`urgency_${urgency}`)}
              </span>
              {patient?.triage && (
                <TriageWhy triage={patient.triage} queuedAt={patient.queued_at ?? null} />
              )}
            </div>
          </div>
        </div>
        <div className="cx-actions no-print">
          <button
            type="button"
            className="cx-btn cx-pdf"
            disabled={exportingPdf}
            aria-label={exportingPdf ? t("exportingPdf") : t("exportPdf")}
            title={t("exportPdf")}
            onClick={() => void exportPdf()}
          >
            <Download aria-hidden="true" size={15} />
            <span className="cx-label-long" aria-hidden="true">
              {exportingPdf ? t("exportingPdf") : t("exportPdf")}
            </span>
            <span className="cx-label-short" aria-hidden="true">PDF</span>
          </button>
          {primary}
          <Popover
            align="end"
            triggerClassName="cx-icon-button cx-more"
            panelClassName="cx-menu"
            ariaLabel={t("moreActions")}
            label={<MoreHorizontal aria-hidden="true" size={18} />}
          >
            {(closeMenu) => (
              <>
                {visitStatus === null && (
                  <button
                    type="button"
                    className="cx-menu-item"
                    onClick={() => {
                      closeMenu();
                      void transition("complete");
                    }}
                  >
                    <CheckCircle2 aria-hidden="true" size={15} /> {t("finishConsult")}
                  </button>
                )}
                {visitStatus === "called" && (
                  <button
                    type="button"
                    className="cx-menu-item"
                    onClick={() => {
                      closeMenu();
                      void transition("return");
                    }}
                  >
                    <Undo2 aria-hidden="true" size={15} /> {t("putBack")}
                  </button>
                )}
                <button
                  type="button"
                  className="cx-menu-item"
                  onClick={() => {
                    closeMenu();
                    printSheet();
                  }}
                >
                  <Printer aria-hidden="true" size={15} /> {t("printCaseSheet")}
                </button>
                <div className="cx-menu-section">
                  <strong>
                    <Keyboard aria-hidden="true" size={14} /> {t("keyboardShortcuts")}
                  </strong>
                  <dl className="cx-keys">
                    <div>
                      <dt><kbd>N</kbd></dt>
                      <dd>{t("shortcutQueue")}</dd>
                    </div>
                    <div>
                      <dt><kbd>E</kbd></dt>
                      <dd>{t("shortcutEdit")}</dd>
                    </div>
                    <div>
                      <dt><kbd>P</kbd></dt>
                      <dd>{t("shortcutPrint")}</dd>
                    </div>
                  </dl>
                </div>
              </>
            )}
          </Popover>
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
          <span className="cx-strip-warn">
            <Clock3 aria-hidden="true" size={15} /> {t("patientConfirmationPending")}
          </span>
        )}
        {data.patient_confirmation?.needs_assistance && (
          <span className="cx-strip-warn">
            <Hand aria-hidden="true" size={15} /> {t("patientNeedsAssistance")}
          </span>
        )}
        {aiFieldsPending ? (
          <span className="cx-strip-ai">
            <Sparkles aria-hidden="true" size={14} /> {t("aiAssistedNote")}
          </span>
        ) : (
          data.ai_summary?.brief_en && (
            <span className="cx-strip-ai">
              <Sparkles aria-hidden="true" size={14} /> {t("aiSummaryUnverified")}
            </span>
          )
        )}
      </div>
      {error && <p className="cx-error" role="alert">{error}</p>}

      {printing ? (
        <article className="cx-sheet">
          {redFlagCard}
          {aiCard}
          {historyCards}
          <p className="cx-disclaimer">{data.disclaimer}</p>
        </article>
      ) : (
        <>
          <div className="cx-tabs no-print" role="tablist" aria-label={t("consultTabs")}>
            {TABS.map((item) => (
              <button
                key={item.id}
                ref={(node) => {
                  tabRefs.current[item.id] = node;
                }}
                id={`${baseId}-tab-${item.id}`}
                type="button"
                role="tab"
                className="cx-tab"
                aria-selected={tab === item.id}
                aria-controls={`${baseId}-panel`}
                tabIndex={tab === item.id ? 0 : -1}
                onClick={() => openTab(item.id)}
                onKeyDown={onTabKey}
              >
                {t(item.label)}
                {item.id === "overview" && redFlags.length > 0 && (
                  <span className="cx-tab-badge cx-tab-badge--danger">
                    <AlertTriangle aria-hidden="true" size={11} />
                    {redFlags.length}
                    <span className="cx-visually-hidden"> {t("redFlags")}</span>
                  </span>
                )}
                {item.id === "transcript" && transcript.length > 0 && (
                  <span className="cx-tab-badge">{transcript.length}</span>
                )}
                {item.id === "previous" && (patientHistory?.previous_visits.length ?? 0) > 0 && (
                  <span className="cx-tab-badge">{patientHistory?.previous_visits.length}</span>
                )}
                {item.id === "history" && dirty && (
                  <span className="cx-tab-dot">
                    <span className="cx-visually-hidden">{t("unsavedEdit")}</span>
                  </span>
                )}
              </button>
            ))}
          </div>
          <div
            className={`cx-panel cx-panel--${tab}`}
            id={`${baseId}-panel`}
            role="tabpanel"
            aria-labelledby={`${baseId}-tab-${tab}`}
          >
            {panel}
          </div>
        </>
      )}

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
    </>,
  );
}
