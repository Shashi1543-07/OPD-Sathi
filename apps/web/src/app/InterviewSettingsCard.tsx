import { useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { ListChecks } from "lucide-react";
import { staffAuth } from "../lib/auth";

type Switch = "review_of_systems_module" | "ayurveda_module";
type InterviewSettings = Record<Switch, boolean> & {
  updated_at: string | null;
  updated_by_name: string | null;
};

/** Each optional module, with its label and what it adds; all are off until a hospital turns them on. */
const SWITCHES: { key: Switch; label: string; hint: string }[] = [
  {
    key: "review_of_systems_module",
    label: "reviewOfSystemsModuleLabel",
    hint: "reviewOfSystemsModuleHint",
  },
  { key: "ayurveda_module", label: "ayurvedaModuleLabel", hint: "ayurvedaModuleHint" },
];

/** Optional interview modules for this hospital: the review of systems and the Ayurveda history. */
export function InterviewSettingsCard() {
  const { t } = useTranslation("staff");
  const titleId = useId();
  const [settings, setSettings] = useState<InterviewSettings>();
  const [values, setValues] = useState<Record<Switch, boolean>>({
    review_of_systems_module: false,
    ayurveda_module: false,
  });
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  useEffect(() => {
    let active = true;
    staffAuth
      .request("/admin/interview-settings")
      .then(async (response) => {
        if (!response.ok) return;
        const body = (await response.json()) as InterviewSettings;
        if (!active) return;
        setSettings(body);
        setValues({
          review_of_systems_module: body.review_of_systems_module,
          ayurveda_module: body.ayurveda_module,
        });
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  const changes = settings
    ? Object.fromEntries(
        SWITCHES.filter(({ key }) => values[key] !== settings[key]).map(({ key }) => [key, values[key]]),
      )
    : {};

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("saving");
    try {
      const response = await staffAuth.request("/admin/interview-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
      if (!response.ok) throw new Error("interview settings not saved");
      const body = (await response.json()) as InterviewSettings;
      setSettings(body);
      setValues({
        review_of_systems_module: body.review_of_systems_module,
        ayurveda_module: body.ayurveda_module,
      });
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  return (
    <form className="adm-card adm-form" aria-labelledby={titleId} onSubmit={(event) => void save(event)}>
      <div className="adm-card-head">
        <span className="adm-card-icon" aria-hidden="true">
          <ListChecks size={20} />
        </span>
        <div>
          <h3 id={titleId}>{t("interviewSettingsTitle")}</h3>
          <p>{t("interviewSettingsHint")}</p>
        </div>
      </div>
      {SWITCHES.map(({ key, label, hint }) => (
        <div key={key} className="adm-switch">
          <label className="adm-check">
            <input
              type="checkbox"
              checked={values[key]}
              disabled={!settings}
              aria-describedby={`${titleId}-${key}`}
              onChange={(event) => {
                setValues((current) => ({ ...current, [key]: event.target.checked }));
                setState("idle");
              }}
            />
            <span>{t(label)}</span>
          </label>
          <small id={`${titleId}-${key}`}>{t(hint)}</small>
        </div>
      ))}
      {state === "saved" && (
        <p className="adm-banner adm-banner--ok" role="status">
          {t("interviewSettingsSaved")}
        </p>
      )}
      {state === "failed" && (
        <p className="adm-banner adm-banner--error" role="alert">
          {t("interviewSettingsFailed")}
        </p>
      )}
      <button
        type="submit"
        className="adm-submit"
        disabled={!settings || state === "saving" || Object.keys(changes).length === 0}
      >
        {t("interviewSettingsSave")}
      </button>
    </form>
  );
}
