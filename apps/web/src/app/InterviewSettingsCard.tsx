import { useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { Leaf } from "lucide-react";
import { staffAuth } from "../lib/auth";

type InterviewSettings = {
  ayurveda_module: boolean;
  updated_at: string | null;
  updated_by_name: string | null;
};

/** Optional interview modules for this hospital: the Ayurveda history at AYUSH hospitals. */
export function InterviewSettingsCard() {
  const { t } = useTranslation("staff");
  const titleId = useId();
  const [settings, setSettings] = useState<InterviewSettings>();
  const [ayurveda, setAyurveda] = useState(false);
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
        setAyurveda(body.ayurveda_module);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("saving");
    try {
      const response = await staffAuth.request("/admin/interview-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ayurveda_module: ayurveda }),
      });
      if (!response.ok) throw new Error("interview settings not saved");
      const body = (await response.json()) as InterviewSettings;
      setSettings(body);
      setAyurveda(body.ayurveda_module);
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  return (
    <form className="adm-card adm-form" aria-labelledby={titleId} onSubmit={(event) => void save(event)}>
      <div className="adm-card-head">
        <span className="adm-card-icon" aria-hidden="true">
          <Leaf size={20} />
        </span>
        <div>
          <h3 id={titleId}>{t("interviewSettingsTitle")}</h3>
          <p>{t("ayurvedaModuleHint")}</p>
        </div>
      </div>
      <label className="adm-check">
        <input
          type="checkbox"
          checked={ayurveda}
          disabled={!settings}
          onChange={(event) => {
            setAyurveda(event.target.checked);
            setState("idle");
          }}
        />
        <span>{t("ayurvedaModuleLabel")}</span>
      </label>
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
        disabled={!settings || state === "saving" || ayurveda === settings.ayurveda_module}
      >
        {t("interviewSettingsSave")}
      </button>
    </form>
  );
}
