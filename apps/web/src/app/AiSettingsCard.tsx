import { useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { Sparkles } from "lucide-react";
import { staffAuth } from "../lib/auth";

type AiSettings = {
  llm_enabled: boolean;
  /** An administrator saved a choice; without one AI is on where the server has it. */
  chosen: boolean;
  /** The server has AI turned on and a provider configured; otherwise the switch does nothing. */
  available: boolean;
};

/**
 * The hospital's switch for online AI help (task 7.11.5). AI works on a visit only when this
 * is on, the server has it, and the patient agreed; everything else runs on this server.
 */
export function AiSettingsCard() {
  const { t } = useTranslation("staff");
  const titleId = useId();
  const [settings, setSettings] = useState<AiSettings>();
  const [enabled, setEnabled] = useState(false);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  useEffect(() => {
    let active = true;
    staffAuth
      .request("/admin/ai-settings")
      .then(async (response) => {
        if (!response.ok) return;
        const body = (await response.json()) as AiSettings;
        if (!active) return;
        setSettings(body);
        setEnabled(body.llm_enabled);
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
      const response = await staffAuth.request("/admin/ai-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ llm_enabled: enabled }),
      });
      if (!response.ok) throw new Error("AI settings not saved");
      const body = (await response.json()) as AiSettings;
      setSettings(body);
      setEnabled(body.llm_enabled);
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  return (
    <form className="adm-card adm-form" aria-labelledby={titleId} onSubmit={(event) => void save(event)}>
      <div className="adm-card-head">
        <span className="adm-card-icon" aria-hidden="true">
          <Sparkles size={20} />
        </span>
        <div>
          <h3 id={titleId}>{t("aiSettingsTitle")}</h3>
          <p>{t("aiSettingsHint")}</p>
        </div>
      </div>
      <div className="adm-switch">
        <label className="adm-check">
          <input
            type="checkbox"
            checked={enabled}
            disabled={!settings}
            aria-describedby={`${titleId}-status`}
            onChange={(event) => {
              setEnabled(event.target.checked);
              setState("idle");
            }}
          />
          <span>{t("aiSettingsLabel")}</span>
        </label>
        <small id={`${titleId}-status`}>
          {settings && !settings.available
            ? t("aiSettingsUnavailable")
            : settings && !settings.chosen
              ? t("aiSettingsDefault")
              : t("aiSettingsConsent")}
        </small>
      </div>
      {state === "saved" && (
        <p className="adm-banner adm-banner--ok" role="status">
          {t("aiSettingsSaved")}
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
        disabled={
          !settings || state === "saving" || (settings.chosen && enabled === settings.llm_enabled)
        }
      >
        {t("interviewSettingsSave")}
      </button>
    </form>
  );
}
