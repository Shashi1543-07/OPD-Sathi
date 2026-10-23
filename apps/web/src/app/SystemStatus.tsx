import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import "../locales/staff";
import { RefreshCw } from "lucide-react";
import { Button, Toast } from "../components/ui";
import { staffAuth } from "../lib/auth";

type Health = "up" | "down" | "stale" | "unknown";
type KioskState = "online" | "silent" | "not_paired" | "inactive" | "poster";
type SystemStatusBody = {
  version: string;
  environment: string;
  checked_at: string | null;
  database: Health;
  redis: Health;
  worker: Health;
  backup: { last_success: string | null; age_hours: number | null };
  disk_free_ratio: number | null;
  certificate: { expires_at: string | null; days_left: number | null };
  ai: { enabled: boolean; llm: string; speech_to_text: string; text_to_speech: string; sms: string };
  kiosks: { id: string; label: string; state: KioskState; last_seen_at: string | null }[];
};
type Tone = "ok" | "warn" | "bad";

// The pill colours of the patient register: green, amber, red.
const PILL: Record<Tone, string> = {
  ok: "patient-status-pill patient-status-pill--done",
  warn: "patient-status-pill patient-status-pill--queued",
  bad: "patient-status-pill patient-status-pill--cancelled",
};
// The alert thresholds (docs/runbooks/alerts.md), so the page and the alerts agree.
const backupTone = (hours: number | null): Tone => (hours === null ? "bad" : hours > 26 ? "bad" : "ok");
const diskTone = (ratio: number | null): Tone =>
  ratio === null ? "warn" : ratio < 0.1 ? "bad" : ratio < 0.2 ? "warn" : "ok";
const certificateTone = (days: number | null): Tone =>
  days === null ? "warn" : days < 7 ? "bad" : days < 21 ? "warn" : "ok";
const healthTone = (health: Health): Tone => (health === "up" ? "ok" : health === "unknown" ? "warn" : "bad");
const kioskTone: Record<KioskState, Tone> = {
  online: "ok",
  silent: "bad",
  not_paired: "warn",
  inactive: "warn",
  poster: "ok",
};

/**
 * Settings -> System: the installation at a glance for the hospital's administrator
 * (Phase 7, task 7.9.7). Problems here are also alerts to hospital IT; the hint says so.
 */
export function SystemStatus() {
  const { t, i18n } = useTranslation("staff");
  const [status, setStatus] = useState<SystemStatusBody | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await staffAuth.request("/admin/system", { cache: "no-store" });
      if (!response.ok) throw new Error(t("systemLoadError"));
      setStatus((await response.json()) as SystemStatusBody);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("systemLoadError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  const when = (value: string | null) =>
    value
      ? new Intl.DateTimeFormat(i18n.language === "en" ? "en-IN" : i18n.language, {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(new Date(value))
      : t("systemNever");
  const health = (value: Health) => t(`systemHealth_${value}`);
  const row = (label: string, tone: Tone, text: string) => (
    <div className="sys-row">
      <dt>{label}</dt>
      <dd>
        <span className={PILL[tone]}>{text}</span>
      </dd>
    </div>
  );

  return (
    <section className="acct sys" aria-labelledby="system-title">
      <h2 id="system-title">{t("systemTitle")}</h2>
      <p className="acct-who">{t("systemHint")}</p>
      <div className="acct-actions">
        <Button
          type="button"
          variant="secondary"
          icon={<RefreshCw aria-hidden="true" size={16} />}
          busy={loading}
          onClick={() => void load()}
        >
          {t("systemRefresh")}
        </Button>
      </div>
      <div aria-live="polite">{error && <Toast tone="error">{error}</Toast>}</div>
      {status && (
        <>
          <dl className="sys-list">
            {row(t("systemVersion"), "ok", `${status.version} (${status.environment})`)}
            {row(t("systemDatabase"), healthTone(status.database), health(status.database))}
            {row(t("systemRedis"), healthTone(status.redis), health(status.redis))}
            {row(t("systemWorker"), healthTone(status.worker), health(status.worker))}
            {row(
              t("systemBackup"),
              backupTone(status.backup.age_hours),
              status.backup.age_hours === null
                ? t("systemNever")
                : t("systemBackupAge", { hours: status.backup.age_hours, when: when(status.backup.last_success) }),
            )}
            {row(
              t("systemDisk"),
              diskTone(status.disk_free_ratio),
              status.disk_free_ratio === null
                ? t("systemHealth_unknown")
                : t("systemDiskFree", { percent: Math.round(status.disk_free_ratio * 100) }),
            )}
            {row(
              t("systemCertificate"),
              certificateTone(status.certificate.days_left),
              status.certificate.days_left === null
                ? t("systemHealth_unknown")
                : t("systemCertificateDays", { days: status.certificate.days_left }),
            )}
            {row(
              t("systemAi"),
              status.ai.enabled ? "ok" : "warn",
              status.ai.enabled ? t("systemAiOn", { llm: status.ai.llm }) : t("systemAiOff"),
            )}
            {row(
              t("systemVoice"),
              "ok",
              t("systemVoiceModes", { stt: status.ai.speech_to_text, tts: status.ai.text_to_speech }),
            )}
            {row(t("systemSms"), "ok", status.ai.sms)}
          </dl>
          <p className="acct-who">{t("systemChecked", { when: when(status.checked_at) })}</p>
          <h3>{t("systemKiosks")}</h3>
          <div className="adm-scroll" role="region" aria-label={t("systemKiosks")} tabIndex={0}>
            {status.kiosks.length === 0 ? (
              <p className="adm-empty">{t("systemNoKiosks")}</p>
            ) : (
              <table className="adm-table">
                <thead>
                  <tr>
                    <th scope="col">{t("systemKiosk")}</th>
                    <th scope="col">{t("systemKioskState")}</th>
                    <th scope="col">{t("systemLastSeen")}</th>
                  </tr>
                </thead>
                <tbody>
                  {status.kiosks.map((kiosk) => (
                    <tr key={kiosk.id}>
                      <td>{kiosk.label}</td>
                      <td>
                        <span className={PILL[kioskTone[kiosk.state]]}>
                          {t(`systemKiosk_${kiosk.state}`)}
                        </span>
                      </td>
                      <td>{when(kiosk.last_seen_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </section>
  );
}
