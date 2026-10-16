import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Info, Leaf } from "lucide-react";
import "../locales/staff";
import type { components } from "../lib/api-types";
import { staffAuth } from "../lib/auth";

type Ayush = components["schemas"]["AyushHistory"];
type TextPair = { text_original: string; text_en: string } | null | undefined;

/** What the patient reported at the kiosk (Ahara Shakti, Jarana Shakti, Mala, Nidra, ...). */
const REPORTED = ["appetite", "digestion", "bowel", "sleep", "exercise", "diet"] as const;
/** Ashtavidha Pariksha, then Dashavidha Pariksha: entered by the vaidya, never asked. */
const ASHTAVIDHA = ["nadi", "mutra", "mala", "jihva", "shabda", "sparsha", "drik", "akriti"] as const;
const DASHAVIDHA = [
  "prakriti",
  "vikriti",
  "sara",
  "samhanana",
  "pramana",
  "satmya",
  "sattva",
  "vaya",
] as const;
type ExamField = (typeof ASHTAVIDHA)[number] | (typeof DASHAVIDHA)[number];

export type SavedSheet = { version: number; [key: string]: unknown };

/**
 * The Ayurveda history: the patient's own answers, and the vaidya's examination findings.
 * Findings are saved in one versioned edit; a clash with another edit reloads the sheet.
 */
export function AyurvedaPanel({
  visitId,
  ayush,
  version,
  canEdit,
  onSaved,
  onConflict,
}: {
  visitId: string;
  ayush: Ayush;
  version: number;
  canEdit: boolean;
  onSaved: (saved: SavedSheet) => void;
  onConflict: () => void;
}) {
  const { t } = useTranslation("staff");
  const baseId = useId();
  const recorded = (ayush.examination ?? {}) as Partial<Record<ExamField, string>>;
  const [values, setValues] = useState<Partial<Record<ExamField, string>>>(recorded);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed" | "conflict">("idle");

  const changes = [...ASHTAVIDHA, ...DASHAVIDHA].flatMap((key) => {
    const before = recorded[key] ?? "";
    const after = (values[key] ?? "").trim();
    if (before === after) return [];
    const path = `/ayush/examination/${key}`;
    if (!after) return [{ op: "remove", path }];
    return [{ op: before ? "replace" : "add", path, value: after }];
  });

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!changes.length) return;
    setState("saving");
    try {
      const response = await staffAuth.request(`/visits/${visitId}/case-sheet`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json-patch+json",
          "If-Match": `"${version}"`,
        },
        body: JSON.stringify(changes),
      });
      if (response.status === 409) {
        setState("conflict");
        onConflict();
        return;
      }
      if (!response.ok) throw new Error("examination not saved");
      const saved = (await response.json()) as SavedSheet;
      const headerVersion = Number(response.headers.get("ETag")?.replaceAll('"', ""));
      onSaved({ ...saved, version: Number.isFinite(headerVersion) ? headerVersion : saved.version });
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  const reported = REPORTED.map((key) => [key, ayush[key] as TextPair] as const).filter(
    ([, value]) => value,
  );
  const fields = (group: readonly ExamField[]) =>
    group.map((key) => (
      <label key={key} className="ayu-field">
        <span>{t(`ayurExam_${key}`)}</span>
        <input
          value={values[key] ?? ""}
          maxLength={500}
          readOnly={!canEdit}
          onChange={(event) => {
            setValues((current) => ({ ...current, [key]: event.target.value }));
            setState("idle");
          }}
        />
      </label>
    ));

  return (
    <section className="ayu" aria-labelledby={`${baseId}-title`}>
      <header className="ayu-head">
        <h3 id={`${baseId}-title`}>
          <Leaf aria-hidden="true" size={18} /> {t("ayurvedaTitle")}
        </h3>
        <p className="cx-panel-note">
          <Info aria-hidden="true" size={15} />
          {t("ayurvedaDraftNote")}
        </p>
      </header>
      <section className="cx-card" aria-labelledby={`${baseId}-reported`}>
        <h4 id={`${baseId}-reported`}>{t("ayurvedaReported")}</h4>
        {reported.length ? (
          <dl className="ayu-reported">
            {reported.map(([key, value]) => (
              <div key={key}>
                <dt>{t(`ayurReported_${key}`)}</dt>
                <dd>
                  {value?.text_en}
                  {value && value.text_original !== value.text_en && (
                    <small> ({value.text_original})</small>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="cx-none">{t("ayurvedaNothingReported")}</p>
        )}
      </section>
      <form className="cx-card ayu-exam" onSubmit={(event) => void save(event)}>
        <h4>{t("ayurvedaExamination")}</h4>
        <p className="cx-panel-note">{t("ayurvedaExamNote")}</p>
        <fieldset>
          <legend>{t("ayurvedaAshtavidha")}</legend>
          <div className="ayu-grid">{fields(ASHTAVIDHA)}</div>
        </fieldset>
        <fieldset>
          <legend>{t("ayurvedaDashavidha")}</legend>
          <div className="ayu-grid">{fields(DASHAVIDHA)}</div>
        </fieldset>
        {state === "saved" && (
          <p className="cx-strip-ok" role="status">
            {t("ayurvedaSaved")}
          </p>
        )}
        {state === "failed" && (
          <p className="cx-error" role="alert">
            {t("ayurvedaSaveFailed")}
          </p>
        )}
        {state === "conflict" && (
          <p className="cx-error" role="alert">
            {t("ayurvedaConflict")}
          </p>
        )}
        {canEdit && (
          <button
            type="submit"
            className="cx-btn cx-btn--primary"
            disabled={state === "saving" || !changes.length}
          >
            {t("ayurvedaSave")}
          </button>
        )}
      </form>
    </section>
  );
}
