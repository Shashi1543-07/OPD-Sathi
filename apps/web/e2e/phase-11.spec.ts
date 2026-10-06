import AxeBuilder from "@axe-core/playwright";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const screenshots = resolve(process.cwd(), "..", "..", "docs", "screenshots", "phase-11");
const hospitalId = "00000000-0000-7000-8000-000000001101";

test("admin analytics shows scoped trends, editable baseline, and CSV export accessibly", async ({ page }) => {
  await page.route("**/auth/refresh", (route) => route.fulfill({ json: {
    access_token: "synthetic-admin-token",
    token_type: "bearer",
    expires_in: 600,
    user: {
      id: "00000000-0000-7000-8000-000000001102",
      name: "Synthetic Admin",
      email: "admin@example.test",
      role: "admin",
      hospital_id: hospitalId,
    },
  } }));
  await page.route("**/analytics/summary", (route) => route.fulfill({ json: {
    patients: 12,
    average_wait_seconds: 240,
    average_consult_seconds: 360,
    urgent_count: 2,
    language_mix: { hi: 8, en: 4 },
    average_interview_seconds: 90,
    confirmed_without_edits_percent: 75,
    doctor_edit_rate_percent: 25,
    department_load: [{
      department_id: "00000000-0000-7000-8000-000000001103",
      name: { en: "General Medicine", hi: "सामान्य चिकित्सा", mr: "सामान्य औषधोपचार" },
      patients: 12,
      urgent_count: 2,
      average_wait_seconds: 240,
    }],
  } }));
  await page.route("**/analytics/timeseries?days=30", (route) => route.fulfill({ json: {
    days: 30,
    items: [{ service_date: "2026-10-07", patient_count: 12 }],
  } }));
  await page.route("**/analytics/export.csv?days=30", (route) => route.fulfill({
    status: 200,
    contentType: "text/csv",
    body: "service_date,department_en,patients\n2026-10-07,General Medicine,12\n",
  }));

  await page.goto("/staff/reports");
  await expect(page.getByRole("heading", { name: "Reports" })).toBeVisible();
  await expect(page.getByText("General Medicine")).toBeVisible();
  await expect(page.getByRole("img", { name: "Daily patient counts for the last 30 days" })).toBeVisible();
  await expect(page.getByLabel("Editable minutes saved per patient")).toHaveValue("3");

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const serious = accessibility.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""));
  console.log(`phase11_analytics_axe_serious_critical=${serious.length}`);
  expect(serious).toEqual([]);

  mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: resolve(screenshots, "admin-analytics.png"), fullPage: true });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download 30-day CSV" }).click();
  expect((await download).suggestedFilename()).toBe("opd-analytics.csv");
});
