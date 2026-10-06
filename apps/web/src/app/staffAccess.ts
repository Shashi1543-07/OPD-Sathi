import type { StaffRole } from "../lib/auth";

export type StaffPageId =
  "dashboard" | "queue" | "consultations" | "patients" | "reports" | "settings";

export const navigationByRole: Record<StaffRole, StaffPageId[]> = {
  admin: [
    "dashboard",
    "queue",
    "consultations",
    "patients",
    "reports",
    "settings",
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
  return role === "admin" ? "dashboard" : "queue";
}
