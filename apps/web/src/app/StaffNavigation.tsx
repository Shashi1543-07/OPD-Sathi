import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { staffAuth } from "../lib/auth";
import {
  canAccessStaffPage,
  defaultStaffPage,
  type StaffPageId,
} from "./staffAccess";

export function StaffLanding() {
  const location = useLocation();
  const user = staffAuth.user;
  if (!user) return <Navigate to="/staff" replace />;
  return (
    <Navigate
      to={{
        pathname: `/staff/${defaultStaffPage(user.role)}`,
        search: location.search,
      }}
      replace
    />
  );
}

export function StaffPageGate({
  page,
  children,
}: {
  page: StaffPageId;
  children: ReactNode;
}) {
  const location = useLocation();
  const user = staffAuth.user;
  if (!user) return <Navigate to="/staff" replace />;
  if (!canAccessStaffPage(user.role, page)) {
    return (
      <Navigate
        to={{
          pathname: `/staff/${defaultStaffPage(user.role)}`,
          search: location.search,
        }}
        replace
      />
    );
  }
  return children;
}
