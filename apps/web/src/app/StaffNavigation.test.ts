import { describe, expect, it } from "vitest";
import {
  canAccessStaffPage,
  defaultStaffPage,
  navigationByRole,
  type StaffPageId,
} from "./staffAccess";

const pages: StaffPageId[] = [
  "dashboard",
  "queue",
  "consultations",
  "patients",
  "reports",
  "settings",
];

describe("staff page role access", () => {
  it("allows exactly the pages listed for each staff role", () => {
    for (const role of ["admin", "doctor", "nurse"] as const) {
      for (const page of pages) {
        expect(canAccessStaffPage(role, page)).toBe(
          navigationByRole[role].includes(page),
        );
      }
    }
  });

  it("provides a role-appropriate landing page", () => {
    expect(defaultStaffPage("admin")).toBe("dashboard");
    expect(defaultStaffPage("doctor")).toBe("queue");
    expect(defaultStaffPage("nurse")).toBe("queue");
  });
});
