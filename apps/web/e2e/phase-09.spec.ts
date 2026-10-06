import AxeBuilder from "@axe-core/playwright";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-000000000901";
const sheet = {
  schema_version: "1.0",
  language: "en",
  chief_complaint: {
    text_original: "Synthetic headache",
    text_en: "Synthetic headache",
    confidence: 0.94,
  },
  hpi: {
    onset: {
      text_original: "since yesterday",
      text_en: "since yesterday",
      confidence: 0.91,
    },
    associated_symptoms: [],
  },
  past_history: { conditions: [], surgeries: [] },
  medications: [],
  allergies: [],
  family_history: [],
  social: {},
  red_flags: [],
  routing: {
    department_code: "GENMED",
    confidence: 0.9,
    reason_en: "Synthetic route.",
    needs_nurse_confirmation: false,
  },
  field_meta: {
    "/hpi/onset/text_original": {
      source: "patient_voice",
      confidence: 0.91,
      confirmed: false,
      ai_assisted: true,
    },
  },
  disclaimer: "History only. Not a diagnosis.",
};

test("staff case sheet shows source, confidence and AI assistance accessibly", async ({
  page,
}) => {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "synthetic-doctor-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-000000000921",
          name: "Synthetic Doctor",
          email: "doctor@example.test",
          role: "doctor",
          hospital_id: "00000000-0000-7000-8000-000000000900",
        },
      },
    }),
  );
  await page.route(`**/visits/${visitId}/transcript`, (route) =>
    route.fulfill({ json: { visit_id: visitId, turns: [] } }),
  );
  await page.route(`**/visits/${visitId}/case-sheet`, (route) =>
    route.fulfill({
      json: { visit_id: visitId, version: 1, case_sheet: sheet },
      headers: { ETag: '"1"' },
    }),
  );

  await page.goto(`/staff/consultations?visit_id=${visitId}&status=called`);
  await expect(page.getByText("Patient voice")).toBeVisible();
  await expect(page.getByText("91%")).toBeVisible();
  await expect(page.getByText("AI-assisted")).toBeVisible();
  await expect(
    page.getByText(
      "Some history fields were organized with AI assistance and need staff review.",
    ),
  ).toBeVisible();

  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(
    result.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);

  const screenshotDir = resolve(
    process.cwd(),
    "..",
    "..",
    "docs",
    "screenshots",
    "phase-09",
  );
  mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: resolve(screenshotDir, "staff-case-sheet.png"),
    fullPage: true,
  });
});
