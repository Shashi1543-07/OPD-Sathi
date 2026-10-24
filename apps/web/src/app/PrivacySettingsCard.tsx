import { useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { ExternalLink, Printer, ShieldCheck } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import i18n, { type Language } from "../i18n";
import { staffAuth } from "../lib/auth";

type PrivacySettings = {
  grievance_contact: string;
  notice_path: string;
  notice_url: string | null;
  updated_at: string | null;
  updated_by_name: string | null;
};

const POSTER_LANGUAGES: Language[] = ["hi", "mr", "en"];

/**
 * The hospital's privacy notice (task 7.5.2): whom patients complain to, a link to the notice
 * and the waiting-area poster with its QR, in all three languages whatever the screen's.
 */
export function PrivacySettingsCard() {
  const { t } = useTranslation("staff");
  const titleId = useId();
  const [settings, setSettings] = useState<PrivacySettings>();
  const [contact, setContact] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const [poster, setPoster] = useState(false);

  useEffect(() => {
    let active = true;
    staffAuth
      .request("/admin/privacy-settings")
      .then(async (response) => {
        if (!response.ok) return;
        const body = (await response.json()) as PrivacySettings;
        if (!active) return;
        setSettings(body);
        setContact(body.grievance_contact);
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
      const response = await staffAuth.request("/admin/privacy-settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ grievance_contact: contact }),
      });
      if (!response.ok) throw new Error("privacy settings not saved");
      const body = (await response.json()) as PrivacySettings;
      setSettings(body);
      setContact(body.grievance_contact);
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  const changed = settings !== undefined && contact.trim() !== settings.grievance_contact;
  return (
    <form className="adm-card adm-form" aria-labelledby={titleId} onSubmit={(event) => void save(event)}>
      <div className="adm-card-head">
        <span className="adm-card-icon" aria-hidden="true">
          <ShieldCheck size={20} />
        </span>
        <div>
          <h3 id={titleId}>{t("privacySettingsTitle")}</h3>
          <p>{t("privacySettingsHint")}</p>
        </div>
      </div>
      <label>
        <span>{t("privacyContactLabel")}</span>
        <textarea
          rows={3}
          maxLength={500}
          value={contact}
          disabled={!settings}
          placeholder={t("privacyContactPlaceholder")}
          onChange={(event) => {
            setContact(event.target.value);
            setState("idle");
          }}
        />
      </label>
      {state === "saved" && (
        <p className="adm-banner adm-banner--ok" role="status">
          {t("privacySettingsSaved")}
        </p>
      )}
      {state === "failed" && (
        <p className="adm-banner adm-banner--error" role="alert">
          {t("privacySettingsFailed")}
        </p>
      )}
      <div className="adm-activation-actions">
        <button type="submit" className="adm-submit" disabled={!changed || state === "saving"}>
          {t("privacySettingsSave")}
        </button>
        {settings && (
          <>
            <a href={settings.notice_path} target="_blank" rel="noreferrer">
              <ExternalLink aria-hidden="true" size={15} /> {t("privacyOpenNotice")}
            </a>
            <button type="button" onClick={() => setPoster((open) => !open)} aria-expanded={poster}>
              <Printer aria-hidden="true" size={15} /> {t("privacyPoster")}
            </button>
          </>
        )}
      </div>
      {settings && poster && (
        <aside className="adm-activation adm-checkin-qr adm-privacy-poster" aria-live="polite">
          {POSTER_LANGUAGES.map((language) => {
            const tl = i18n.getFixedT(language, "status");
            return (
              <div key={language} lang={language} className="adm-privacy-poster-language">
                <h3>{tl("privacyPosterTitle")}</h3>
                <p>{tl("privacyPosterBody")}</p>
              </div>
            );
          })}
          {settings.notice_url ? (
            <>
              <div className="adm-checkin-qr-code">
                <QRCodeSVG
                  value={settings.notice_url}
                  size={220}
                  level="M"
                  marginSize={2}
                  title={t("privacySettingsTitle")}
                />
              </div>
              <code>{settings.notice_url}</code>
              {POSTER_LANGUAGES.map((language) => (
                <p key={language} lang={language} className="adm-privacy-poster-desk">
                  {i18n.getFixedT(language, "status")("privacyPosterDesk")}
                </p>
              ))}
              <div className="adm-activation-actions no-print">
                <button type="button" onClick={() => window.print()}>
                  <Printer aria-hidden="true" size={15} /> {t("privacyPosterPrint")}
                </button>
              </div>
            </>
          ) : (
            <p className="adm-banner adm-banner--error" role="alert">
              {t("privacyPosterNoAddress")}
            </p>
          )}
        </aside>
      )}
    </form>
  );
}
