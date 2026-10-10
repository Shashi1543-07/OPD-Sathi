import { useTranslation } from "react-i18next";
import { BellRing, TriangleAlert } from "lucide-react";

/**
 * Shown after the emergency button or a "Get help now" on the safety screen. The
 * instructions appear even if the alert could not be sent: no one should wait for a
 * network call in a life-threatening situation.
 */
export function EmergencyNotice({
  state,
  onDone,
}: {
  state: "sending" | "sent" | "failed";
  onDone: () => void;
}) {
  const { t } = useTranslation("kiosk");
  return (
    <section className="emergency-notice" role="alert" aria-live="assertive">
      {state === "failed" ? (
        <TriangleAlert aria-hidden="true" size={48} />
      ) : (
        <BellRing aria-hidden="true" size={48} />
      )}
      <h1>{state === "failed" ? t("emergencyFailed") : t("emergencySentTitle")}</h1>
      {state !== "failed" && <p>{t("emergencySentBody")}</p>}
      <p className="emergency-casualty">{t("emergencyCasualty")}</p>
      <button
        type="button"
        className="ui-button ui-button--secondary ui-button--kiosk"
        onClick={onDone}
      >
        {t("emergencyDone")}
      </button>
    </section>
  );
}
