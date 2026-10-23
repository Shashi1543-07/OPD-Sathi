import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-00000000c001";
const longAnswer =
  "Khansi teen din se hai, subah balgam mein thoda khoon dikha. Raat ko thand lagti hai aur gala kharab hai. " +
  "Ghar mein bhai ka TB ka ilaaj chal raha hai, isliye dar lag raha hai. Pehle BP ki dawai li thi par ab band hai. " +
  "Seedhi chadhne par saans thodi phoolti hai, bukhar naapa nahi.";
const sheet = {
  schema_version: "1.0",
  language: "hi",
  chief_complaint: { text_original: "khansi", text_en: "Cough", confidence: 0.93 },
  hpi: {
    onset: { text_original: "teen din", text_en: "1–3 days", confidence: 0.9 },
    severity_0_10: 9,
    associated_symptoms: [
      { text_original: "balgam mein khoon", text_en: "Blood in phlegm", confidence: 0.88 },
      { text_original: "gala kharab", text_en: "Scratchy throat", confidence: 0.9 },
      { text_original: "thand lagna", text_en: "Chills", confidence: 0.86 },
    ],
    pertinent_negatives: [],
  },
  past_history: { conditions: ["High blood pressure"], surgeries: [] },
  medications: [],
  allergies: [],
  family_history: [
    { text_original: "bhai ko TB", text_en: "Household tuberculosis contact (brother)", confidence: 0.9 },
  ],
  social: {},
  red_flags: [
    {
      id: "RF_HAEMOPTYSIS",
      level: "urgent",
      source: "rule",
      evidence: "balgam mein khoon",
      reason_en: "Blood in phlegm reported",
    },
    {
      id: "RF_TB_CONTACT",
      level: "priority",
      source: "rule",
      evidence: "bhai ko TB",
      reason_en: "Household tuberculosis contact reported",
    },
  ],
  routing: {
    department_code: "GENMED",
    confidence: 0.9,
    reason_en: "Synthetic route.",
    needs_nurse_confirmation: false,
  },
  field_meta: {
    "/chief_complaint": { source: "patient_voice", confidence: 0.93, confirmed: true, ai_assisted: true },
    "/family_history/0": { source: "patient_voice", confidence: 0.9, confirmed: true, ai_assisted: true },
  },
  ai_summary: {
    brief_en:
      "Patient reports cough for 1–3 days with blood-tinged phlegm, scratchy throat and chills. History includes high blood pressure and a household tuberculosis contact.",
  },
  patient_confirmation: { confirmed_by_patient: true, corrections: [], needs_assistance: false },
  disclaimer: "History only. Not a diagnosis.",
};
const queueItem = (n: number, extra: Record<string, unknown> = {}) => ({
  visit_id: n === 1 ? visitId : `00000000-0000-7000-8000-00000000c00${n}`,
  department_id: "00000000-0000-7000-8000-000000000d10",
  token: `MED-00${n}`,
  urgency: "routine",
  status: "queued",
  age_years: 30 + n,
  sex: "female",
  complaint: "Fever and body ache",
  position: n,
  queued_at: "2026-10-10T08:00:00Z",
  ...extra,
});

async function signedInDoctor(page: Page) {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "doctor-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-000000000d03",
          name: "Demo Doctor",
          email: "doctor@example.test",
          role: "doctor",
          hospital_id: "00000000-0000-7000-8000-000000000d00",
        },
      },
    }),
  );
  await page.route(/\/queue(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        items: [
          queueItem(1, { age_years: 25, sex: "male", complaint: "Cough", urgency: "urgent" }),
          queueItem(2),
          queueItem(3, { complaint: "Knee pain after a fall while climbing the stairs at home" }),
        ],
        departments: [],
        next_cursor: null,
        limit: 50,
      },
    }),
  );
  await page.route(`**/visits/${visitId}/case-sheet`, (route) =>
    route.fulfill({
      json: {
        visit_id: visitId,
        version: 2,
        case_sheet: sheet,
        confirmed_by_patient: true,
        confirmed_at: "2026-10-10T12:04:00Z",
        patient: {
          token: "MED-001",
          age_years: 25,
          sex: "male",
          language: "hi",
          urgency: "urgent",
          department_name: "General Medicine",
        },
      },
      headers: { ETag: '"2"' },
    }),
  );
  await page.route(`**/visits/${visitId}/transcript`, (route) =>
    route.fulfill({
      json: {
        visit_id: visitId,
        turns: [
          { turn_index: 1, question_id: "chief_complaint", input_mode: "voice", text_original: "khansi", text_english: "Cough", stt_confidence: 0.93 },
          { turn_index: 2, question_id: "general_context", input_mode: "voice", text_original: longAnswer, text_english: null, stt_confidence: 0.81 },
        ],
      },
    }),
  );
}

async function overflow(page: Page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const main = document.querySelector(".staff-main");
    const sticking = [...document.querySelectorAll(".cx *")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.right > vw + 1;
      })
      .map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].join(".")}`)
      .slice(0, 3);
    return {
      page: document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth,
      main: main ? main.scrollWidth - main.clientWidth : 0,
      sticking,
    };
  });
}

const WIDTHS = [1600, 1440, 1366, 1024, 768, 390, 320];

test("an open case sheet fits every screen, in every section", async ({ page }) => {
  await signedInDoctor(page);
  await page.goto(`/staff/consultations?visit_id=${visitId}&status=queued`);
  const flags = page.getByRole("region", { name: "Red flags — review required" });
  await expect(flags).toContainText("Blood in phlegm reported");
  await expect(flags).toContainText("Household tuberculosis contact reported");
  await expect(page.getByRole("button", { name: "Call patient" })).toBeVisible();

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 });
    for (const tab of ["Overview", "Patient history", "Interview transcript", "Clinical notes"]) {
      await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
      await expect
        .poll(() => overflow(page), { message: `${tab} at ${width}px` })
        .toEqual({ page: 0, main: 0, sticking: [] });
    }
    await page.getByRole("tab", { name: /^Overview/ }).click();
  }

  // The long spoken answer is shown whole, not cut off.
  await page.getByRole("tab", { name: /^Interview transcript/ }).click();
  await expect(page.getByText(longAnswer)).toBeVisible();
});

test("phones fold the patient list into a drawer that keeps the open patient", async ({ page }) => {
  await signedInDoctor(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/staff/consultations");
  await expect(page.getByRole("heading", { name: "Select a patient to begin" })).toBeVisible();
  // With nobody open, the list is shown straight away.
  const list = page.getByRole("navigation", { name: "Today's patients" });
  await expect(list.getByRole("link", { name: /MED-002/ })).toBeVisible();
  expect(await overflow(page)).toEqual({ page: 0, main: 0, sticking: [] });

  await list.getByRole("link", { name: /MED-001/ }).click();
  await expect(page.getByRole("region", { name: "Red flags — review required" })).toBeVisible();
  // Opening a patient folds the list; reopening it leaves the case sheet where it was.
  await expect(list.getByRole("link", { name: /MED-002/ })).toBeHidden();
  await page.getByRole("tab", { name: /^Interview transcript/ }).click();
  await list.getByRole("button", { name: "Show list" }).click();
  await expect(list.getByRole("link", { name: /MED-002/ })).toBeVisible();
  await list.getByRole("button", { name: "Hide list" }).click();
  await expect(page.getByRole("tab", { name: /^Interview transcript/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 });
    await page.goto("/staff/consultations");
    await expect(page.getByRole("heading", { name: "Select a patient to begin" })).toBeVisible();
    expect(await overflow(page), `no patient at ${width}px`).toEqual({ page: 0, main: 0, sticking: [] });
  }
});

test("the case sheet sections pass an accessibility scan", async ({ page }) => {
  await signedInDoctor(page);
  await page.goto(`/staff/consultations?visit_id=${visitId}&status=in_consult`);
  await expect(page.getByRole("button", { name: "Finish consultation" })).toBeVisible();
  for (const tab of ["Overview", "Interview transcript", "Clinical notes"]) {
    await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const serious = result.violations.filter((violation) =>
      ["serious", "critical"].includes(violation.impact ?? ""),
    );
    expect(serious.map((violation) => `${violation.id}: ${violation.nodes[0]?.target}`), tab).toEqual([]);
  }
});
