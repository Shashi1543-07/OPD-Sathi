import { useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { ToggleRight } from "lucide-react";
import { staffAuth } from "../lib/auth";

type Switch = "sms_messages" | "phone_checkin";
type FeatureSettings = Record<Switch, boolean> & {
  sms_provider: boolean;
  updated_at: string | null;
  updated_by_name: string | null;
};

/** Each hospital-wide feature, with its label and what switching it changes (task 7.14.7). */
const SWITCHES: { key: Switch; label: string; hint: string }[] = [
  { key: "sms_messages", label: "smsMessagesLabel", hint: "smsMessagesHint" },
  { key: "phone_checkin", label: "phoneCheckinLabel", hint: "phoneCheckinHint" },
];

/** Features this hospital switches on or off: SMS queue messages and phone check-in. */
export function FeatureSettingsCard() {
  const { t } = useTranslation("staff");
  const titleId = useId();
  const [settings, setSettings] = useState<FeatureSettings>();
  const [values, setValues] = useState<Record<Switch, boolean>>({
    sms_messages: false,
    phone_checkin: true,
  });
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  function show(body: FeatureSettings) {
    setSettings(body);
    setValues({ sms_messages: body.sms_messages, phone_checkin: body.phone_checkin });
  }

  useEffect(() => {
    let active = true;
    staffAuth
      .request("/admin/feature-settings")
      .then(async (response) => {
        if (!response.ok) return;
        const body = (await response.json()) as FeatureSettings;
        if (active) show(body);
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
      const response = await staffAuth.request("/admin/feature-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
      if (!response.ok) throw new Error("feature settings not saved");
      show((await response.json()) as FeatureSettings);
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  return (
    <form className="adm-card adm-form" aria-labelledby={titleId} onSubmit={(event) => void save(event)}>
      <div className="adm-card-head">
        <span className="adm-card-icon" aria-hidden="true">
          <ToggleRight size={20} />
        </span>
        <div>
          <h3 id={titleId}>{t("featureSettingsTitle")}</h3>
          <p>{t("featureSettingsHint")}</p>
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
          <small id={`${titleId}-${key}`}>
            {t(hint)}
            {/* Switched on without a provider, the server only writes messages to its log. */}
            {key === "sms_messages" && settings && !settings.sms_provider && ` ${t("smsNoProvider")}`}
          </small>
        </div>
      ))}
      {state === "saved" && (
        <p className="adm-banner adm-banner--ok" role="status">
          {t("featureSettingsSaved")}
        </p>
      )}
      {state === "failed" && (
        <p className="adm-banner adm-banner--error" role="alert">
          {t("featureSettingsFailed")}
        </p>
      )}
      <button
        type="submit"
        className="adm-submit"
        disabled={!settings || state === "saving" || Object.keys(changes).length === 0}
      >
        {t("featureSettingsSave")}
      </button>
    </form>
  );
}
