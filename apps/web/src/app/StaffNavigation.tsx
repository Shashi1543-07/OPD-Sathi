import { Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import { staffAuth } from "../lib/auth";
import {
  canAccessStaffPage,
  defaultStaffPage,
  type StaffPageId,
} from "./staffAccess";

export function StaffLanding() {
  const user = staffAuth.user;
  if (!user) return <Navigate to="/staff" replace />;
  return <Navigate to={`/staff/${defaultStaffPage(user.role)}`} replace />;
}

export function StaffPageGate({
  page,
  children,
}: {
  page: StaffPageId;
  children: ReactNode;
}) {
  const user = staffAuth.user;
  if (!user) return <Navigate to="/staff" replace />;
  if (!canAccessStaffPage(user.role, page)) {
    return <Navigate to={`/staff/${defaultStaffPage(user.role)}`} replace />;
  }
  return children;
}

export function StaffPlaceholderPage({
  page,
}: {
  page: Exclude<StaffPageId, "queue">;
}) {
  const { t } = useTranslation("staff");
  return (
    <section
      className="staff-page-placeholder"
      aria-labelledby="staff-page-title"
    >
      <h2 id="staff-page-title">{t(page)}</h2>
      <p>{t("sectionPlaceholder")}</p>
    </section>
  );
}
