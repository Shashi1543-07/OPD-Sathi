import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { EmptyState, ProgressDots, UrgencyBadge } from "../components/ui";

export function PatientStatus() {
  const { token } = useParams<{ token: string }>();
  const { t } = useTranslation(["common", "status", "staff"]);
  if (token !== "demo") {
    return (
      <main className="status-page">
        <EmptyState title={t("statusUnavailable", { ns: "status" })}>
          {t("demoOnly", { ns: "status" })}
        </EmptyState>
      </main>
    );
  }
  return (
    <main className="status-page">
      <article className="status-card">
        <p className="eyebrow">{t("appName", { ns: "common" })}</p>
        <span className="preview-label">
          {t("demoNotice", { ns: "common" })}
        </span>
        <h1>{t("queueStatus", { ns: "status" })}</h1>
        <p>{t("tokenLabel", { ns: "status" })}</p>
        <strong className="status-token">DEMO-024</strong>
        <div className="status-divider" />
        <div className="status-number">
          <span>{t("peopleAhead", { ns: "status" })}</span>
          <strong>2</strong>
        </div>
        <div className="status-number">
          <span>{t("estimatedWait", { ns: "status" })}</span>
          <strong>~20 min</strong>
        </div>
        <UrgencyBadge level="routine" />
        <ProgressDots current={2} total={4} />
        <p className="status-updated">{t("statusUpdated", { ns: "status" })}</p>
        <p>{t("historyOnly", { ns: "common" })}</p>
      </article>
    </main>
  );
}
