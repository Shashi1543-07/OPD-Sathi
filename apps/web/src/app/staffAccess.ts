import type { StaffRole } from "../lib/auth";

export type StaffPageId =
  "queue" | "consultations" | "patients" | "reports" | "settings" | "audit" | "system";

export const navigationByRole: Record<StaffRole, StaffPageId[]> = {
  admin: [
    "queue",
    "consultations",
    "patients",
    "reports",
    "settings",
    "audit",
    "system",
  ],
  doctor: ["queue", "consultations", "patients"],
  nurse: ["queue", "patients"],
};

export function canAccessStaffPage(
  role: StaffRole,
  page: StaffPageId,
): boolean {
  return navigationByRole[role].includes(page);
}

export function defaultStaffPage(role: StaffRole): StaffPageId {
  return role === "admin" ? "reports" : "queue";
}
