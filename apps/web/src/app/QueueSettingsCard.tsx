import { useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { Timer } from "lucide-react";
import { staffAuth } from "../lib/auth";

type QueueSettings = {
  call_grace_seconds: number;
  is_default: boolean;
  updated_at: string | null;
  updated_by_name: string | null;
};

/** How long after a call staff wait before they may mark a patient absent (per hospital). */
export function QueueSettingsCard() {
  const { t } = useTranslation("staff");
  const titleId = useId();
  const [settings, setSettings] = useState<QueueSettings>();
  const [minutes, setMinutes] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  useEffect(() => {
    let active = true;
    staffAuth
      .request("/admin/queue-settings")
      .then(async (response) => {
        if (!response.ok) return;
        const body = (await response.json()) as QueueSettings;
        if (!active) return;
        setSettings(body);
        setMinutes(String(body.call_grace_seconds / 60));
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = Number(minutes);
    if (!Number.isFinite(value) || value < 0 || value > 15) return;
    setState("saving");
    try {
      const response = await staffAuth.request("/admin/queue-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ call_grace_seconds: Math.round(value * 60) }),
      });
      if (!response.ok) throw new Error("queue settings not saved");
      const body = (await response.json()) as QueueSettings;
      setSettings(body);
      setMinutes(String(body.call_grace_seconds / 60));
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  return (
    <form
      className="adm-card adm-form"
      aria-labelledby={titleId}
      onSubmit={(event) => void save(event)}
    >
      <div className="adm-card-head">
        <span className="adm-card-icon" aria-hidden="true">
          <Timer size={20} />
        </span>
        <div>
          <h3 id={titleId}>{t("queueSettingsTitle")}</h3>
          <p>{t("callGraceHint")}</p>
        </div>
      </div>
      <label>
        <span>{t("callGraceLabel")}</span>
        <span className="adm-inline-field">
          <input
            type="number"
            min={0}
            max={15}
            step={0.5}
            inputMode="decimal"
            required
            value={minutes}
            disabled={!settings}
            onChange={(event) => {
              setMinutes(event.target.value);
              setState("idle");
            }}
          />
          <span>{t("callGraceMinutes")}</span>
        </span>
        {settings?.is_default && <small>{t("callGraceDefault")}</small>}
      </label>
      {state === "saved" && (
        <p className="adm-banner adm-banner--ok" role="status">
          {t("callGraceSaved")}
        </p>
      )}
      {state === "failed" && (
        <p className="adm-banner adm-banner--error" role="alert">
          {t("callGraceFailed")}
        </p>
      )}
      <button
        type="submit"
        className="adm-submit"
        disabled={!settings || state === "saving"}
      >
        {t("callGraceSave")}
      </button>
    </form>
  );
}
