import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

test("staff patient register is useful at desktop and mobile sizes", async ({
  page,
}) => {
  const screenshotDir = resolve(
    process.cwd(),
    "..",
    "..",
    "docs",
    "screenshots",
    "phase-14",
  );
  mkdirSync(screenshotDir, { recursive: true });
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "synthetic-staff-token",
        token_type: "bearer",
        expires_in: 300,
        user: {
          id: "synthetic-admin-id",
          name: "Demo Administrator",
          email: "admin@demo.opdsathi.test",
          role: "admin",
          hospital_id: "synthetic-hospital-id",
        },
      },
    }),
  );
  await page.route((url) => url.pathname === "/patients", (route) =>
    route.fulfill({
      json: {
        limit: 50,
        items: [
          {
            visit_id: "synthetic-visit-1",
            token: "SAMPLE-001",
            status: "queued",
            urgency: "urgent",
            age_years: 34,
            sex: "female",
            department: "General Medicine",
            registered_at: "2026-10-07T08:30:00Z",
            complaint: "Fever and body aches",
            case_sheet_available: true,
          },
          {
            visit_id: "synthetic-visit-2",
            token: "SAMPLE-002",
            status: "called",
            urgency: "priority",
            age_years: 52,
            sex: "male",
            department: "Cardiology",
            registered_at: "2026-10-07T08:15:00Z",
            complaint: "Chest discomfort while walking",
            case_sheet_available: true,
          },
          {
            visit_id: "synthetic-visit-3",
            token: "SAMPLE-003",
            status: "done",
            urgency: "routine",
            age_years: 27,
            sex: "male",
            department: "Orthopaedics",
            registered_at: "2026-10-07T07:50:00Z",
            complaint: "Ankle pain after a minor twist",
            case_sheet_available: true,
          },
        ],
      },
    }),
  );

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/staff/patients?demo=1");
  await expect(
    page.getByRole("heading", { name: "Recent visits" }),
  ).toBeVisible();
  await expect(page.getByText("SAMPLE-001")).toBeVisible();
  await expect(page.getByText("Fever and body aches")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Open case sheet" }).first(),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "staff-patients-desktop.png"),
    fullPage: true,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(
    page.getByRole("heading", { name: "Recent visits" }),
  ).toBeVisible();
  await expect(page.getByText("SAMPLE-001")).toBeVisible();
  const hasHorizontalPageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(hasHorizontalPageOverflow).toBe(false);
  await page.screenshot({
    path: resolve(screenshotDir, "staff-patients-mobile.png"),
    fullPage: true,
  });
});
