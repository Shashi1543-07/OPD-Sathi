import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useOutletContext } from "react-router-dom";
import { useLanguageStore } from "../languageStore";
import { newRequestId } from "../lib/ids";
import { intake } from "../lib/intake";
import consentCopies from "../lib/offlineConsent.json";
import {
  deferOfflineSubmission,
  offlineSubmissionCount,
  pendingOfflineSubmissions,
  queueOfflineSubmission,
  removeOfflineSubmission,
  type OfflineSubmission,
} from "../lib/offlineOutbox";

const DANGER_IDS = [
  "chest_pain",
  "breathing_difficulty",
  "heavy_bleeding",
  "fainting",
  "sudden_weakness",
  "seizure",
] as const;

type SyncState = { kind: "idle" | "saved" | "failed" } | { kind: "synced"; token: string };

export function KioskOfflineIntake() {
  const { t } = useTranslation("kiosk");
  const language = useLanguageStore((state) => state.language);
  const intakeLanguage = language === "hi" || language === "mr" ? language : "en";
  const consentCopy = consentCopies[intakeLanguage];
  const { networkOffline } = useOutletContext<{ networkOffline: boolean }>();
  const [age, setAge] = useState("");
  const [sex, setSex] = useState<OfflineSubmission["sex"]>("unknown");
  const [complaint, setComplaint] = useState("");
  const [duration, setDuration] = useState("");
  const [severity, setSeverity] = useState(0);
  const [conditions, setConditions] = useState("");
  const [medications, setMedications] = useState("");
  const [allergies, setAllergies] = useState("");
  const [dangerTiles, setDangerTiles] = useState<string[]>([]);
  const [consented, setConsented] = useState(false);
  const [syncState, setSyncState] = useState<SyncState>({ kind: "idle" });
  const [provisionalToken, setProvisionalToken] = useState("");
  const [pendingCount, setPendingCount] = useState(0);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const syncing = useRef(false);
  // The network came back while a sync was running: run once more straight after it.
  const resyncAfter = useRef(false);

  const syncPending = useCallback(async (reconnected = false) => {
    if (!navigator.onLine || !intake.kioskAccessToken()) return;
    if (syncing.current) {
      if (reconnected) resyncAfter.current = true;
      return;
    }
    syncing.current = true;
    let ignoreBackoff = reconnected;
    try {
      do {
        resyncAfter.current = false;
        try {
          const pending = await pendingOfflineSubmissions({ ignoreBackoff });
          for (const { record, submission } of pending) {
            if (!navigator.onLine) break;
            try {
              const response = await intake.submitOffline(submission);
              await removeOfflineSubmission(record.id);
              setSyncState({ kind: "synced", token: response.token });
            } catch {
              await deferOfflineSubmission(record);
              setSyncState({ kind: "failed" });
            }
          }
          setPendingCount(await offlineSubmissionCount());
        } catch {
          setSyncState({ kind: "failed" });
        }
        ignoreBackoff = true;
      } while (resyncAfter.current && navigator.onLine);
    } finally {
      syncing.current = false;
    }
  }, []);

  useEffect(() => {
    void offlineSubmissionCount().then(setPendingCount).catch(() => setFormError(t("offlineSyncFailed")));
    const initialSync = window.setTimeout(() => void syncPending(), 0);
    const timer = window.setInterval(() => void syncPending(), 10_000);
    const reconnected = () => void syncPending(true);
    window.addEventListener("online", reconnected);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(initialSync);
      window.removeEventListener("online", reconnected);
    };
  }, [syncPending, t]);

  function toggleDanger(id: string) {
    setDangerTiles((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    if (!consented) {
      setFormError(t("offlineConsentRequired"));
      return;
    }
    const kioskToken = intake.kioskAccessToken();
    if (!kioskToken) {
      setFormError(t("activationHelp"));
      return;
    }
    setSaving(true);
    try {
      const submission: OfflineSubmission = {
        idempotency_key: newRequestId().replace(/-/g, ""),
        language: intakeLanguage,
        consent_granted: true,
        consent_version: consentCopy.version,
        consent_sha256: consentCopy.sha256,
        age: Number(age),
        sex,
        chief_complaint: complaint.trim(),
        duration: duration.trim(),
        severity,
        conditions: conditions.trim(),
        medications: medications.trim(),
        allergies: allergies.trim(),
        danger_tile_ids: dangerTiles,
      };
      const queued = await queueOfflineSubmission(submission, kioskToken);
      setProvisionalToken(queued.token);
      setPendingCount(await offlineSubmissionCount());
      setSyncState({ kind: "saved" });
      if (dangerTiles.length) setFormError(t("offlineNurseNow"));
      else setFormError("");
      if (navigator.onLine) void syncPending();
      // The local token is visibly provisional until the server returns a real token.
      setSyncState((current) => current.kind === "synced" ? current : { kind: "saved" });
    } catch {
      setFormError(t("offlineSyncFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="kiosk-main kiosk-offline-page">
      <section className="welcome-card kiosk-intake-card" aria-labelledby="offline-intake-title">
        <p className="eyebrow">{networkOffline ? t("offlinePending") : t("screenS4")}</p>
        <h1 id="offline-intake-title">{t("offlineTitle")}</h1>
        <p>{t("offlineDescription")}</p>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="offline-age">{t("offlineAge")}</label>
          <input id="offline-age" type="number" inputMode="numeric" min="0" max="120" required value={age} onChange={(event) => setAge(event.target.value)} />

          <label htmlFor="offline-sex">{t("offlineSex")}</label>
          <select id="offline-sex" value={sex} onChange={(event) => setSex(event.target.value as OfflineSubmission["sex"])}>
            <option value="female">{t("sexFemale")}</option>
            <option value="male">{t("sexMale")}</option>
            <option value="other">{t("sexOther")}</option>
            <option value="unknown">{t("sexUnknown")}</option>
          </select>

          <label htmlFor="offline-complaint">{t("offlineComplaint")}</label>
          <input id="offline-complaint" required maxLength={500} placeholder={t("offlineComplaintHint")} value={complaint} onChange={(event) => setComplaint(event.target.value)} />

          <label htmlFor="offline-duration">{t("offlineDuration")}</label>
          <input id="offline-duration" maxLength={500} value={duration} onChange={(event) => setDuration(event.target.value)} />

          <label htmlFor="offline-severity">{t("offlineSeverity")}: {severity}</label>
          <input id="offline-severity" type="range" min="0" max="10" step="1" value={severity} onChange={(event) => setSeverity(Number(event.target.value))} />

          <label htmlFor="offline-conditions">{t("offlineConditions")}</label>
          <input id="offline-conditions" maxLength={500} value={conditions} onChange={(event) => setConditions(event.target.value)} />

          <label htmlFor="offline-medications">{t("offlineMedications")}</label>
          <input id="offline-medications" maxLength={500} value={medications} onChange={(event) => setMedications(event.target.value)} />

          <label htmlFor="offline-allergies">{t("offlineAllergies")}</label>
          <input id="offline-allergies" maxLength={500} value={allergies} onChange={(event) => setAllergies(event.target.value)} />

          <fieldset className="offline-danger-options">
            <legend>{t("offlineDangerTitle")}</legend>
            {DANGER_IDS.map((id) => <label key={id}>
              <input type="checkbox" checked={dangerTiles.includes(id)} onChange={() => toggleDanger(id)} />
              {t(`offlineDanger_${id}`)}
            </label>)}
          </fieldset>
          {dangerTiles.length > 0 && <p className="offline-nurse-warning" role="alert">{t("offlineNurseNow")}</p>}

          <label className="offline-consent" htmlFor="offline-consent">
            <input id="offline-consent" type="checkbox" required checked={consented} onChange={(event) => setConsented(event.target.checked)} />
            {consentCopy.text}
          </label>
          <button className="ui-button ui-button--primary ui-button--kiosk" type="submit" disabled={saving}>
            {saving ? t("offlinePending") : t("offlineSubmit")}
          </button>
        </form>
        {formError && <p role="alert">{formError}</p>}
        {syncState.kind === "saved" && <p role="status">{t("offlineSaved")}</p>}
        {provisionalToken && syncState.kind !== "synced" && <div className="offline-provisional-slip">
          <strong>{provisionalToken}</strong>
          <span>{t("offlineProvisionalToken")}</span>
          <button className="ui-button ui-button--secondary" type="button" onClick={() => window.print()}>{t("printReceipt")}</button>
        </div>}
        {syncState.kind === "failed" && <p role="status">{t("offlineSyncFailed")}</p>}
        {syncState.kind === "synced" && <p role="status">{t("offlineSynced", { token: syncState.token })}</p>}
        {pendingCount > 0 && <p role="status">{t("offlinePending")}: {pendingCount}</p>}
        <Link className="ui-button ui-button--secondary" to="/kiosk">{t("offlineBack")}</Link>
      </section>
    </main>
  );
}
