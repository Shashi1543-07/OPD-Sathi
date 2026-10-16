import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-00000000f001";
const draft = "52-year-old female reports joint pain for 1-3 days.";

function caseSheet(review: Record<string, unknown> | null, examination: Record<string, string>) {
  return {
    schema_version: "1.0",
    language: "hi",
    chief_complaint: { text_original: "जोड़ों में दर्द", text_en: "Joint pain", confidence: 0.9 },
    hpi: { associated_symptoms: [], pertinent_negatives: [] },
    past_history: { conditions: [], surgeries: [] },
    medications: [],
    allergies: [],
    family_history: [],
    social: {},
    unanswered: [
      { question_id: "habits", status: "declined", text_en: "Preferred not to say: tobacco and alcohol use" },
    ],
    ayush: {
      appetite: { text_original: "पहले से कम", text_en: "Less than before" },
      bowel: { text_original: "सख़्त या कब्ज़", text_en: "Hard or constipated" },
      examination,
    },
    red_flags: [],
    routing: {
      department_code: "GENMED",
      confidence: 0.9,
      reason_en: "Synthetic route.",
      needs_nurse_confirmation: false,
    },
    field_meta: {},
    ai_summary: {
      brief_en: draft,
      statements: [{ text_en: draft, sources: ["chief_concern", "duration"] }],
      answers_en: {},
      review,
    },
    disclaimer: "History only. Not a diagnosis.",
  };
}

async function signedInDoctor(page: Page) {
  let review: Record<string, unknown> | null = null;
  let examination: Record<string, string> = {};
  let version = 3;
  const requests: { path: string; body: unknown; ifMatch: string | null }[] = [];
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "doctor-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-00000000f003",
          name: "Dr Rao",
          email: "doctor@example.test",
          role: "doctor",
          hospital_id: "00000000-0000-7000-8000-00000000f000",
        },
      },
    }),
  );
  await page.route(/\/queue(?:\?.*)?$/, (route) =>
    route.fulfill({ json: { items: [], departments: [], next_cursor: null, limit: 50, missed: [] } }),
  );
  const respond = () => ({
    json: {
      visit_id: visitId,
      version,
      case_sheet: caseSheet(review, examination),
      confirmed_by_patient: true,
      confirmed_at: "2026-10-16T05:00:00Z",
      patient: { token: "MED-021", age_years: 52, sex: "female", language: "hi", urgency: "routine" },
    },
    headers: { ETag: `"${version}"` },
  });
  await page.route(`**/visits/${visitId}/case-sheet`, async (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      const operations = JSON.parse(request.postData() ?? "[]") as { path: string; value?: string }[];
      requests.push({ path: "case-sheet", body: operations, ifMatch: request.headers()["if-match"] ?? null });
      for (const operation of operations) {
        examination = { ...examination, [operation.path.split("/").pop() ?? ""]: operation.value ?? "" };
      }
      version += 1;
    }
    await route.fulfill(respond());
  });
  await page.route(`**/visits/${visitId}/ai-summary/review`, async (route) => {
    const request = route.request();
    requests.push({
      path: "review",
      body: JSON.parse(request.postData() ?? "{}"),
      ifMatch: request.headers()["if-match"] ?? null,
    });
    review = { status: "accepted", by_name: "Dr Rao", at: "2026-10-16T05:10:00Z" };
    version += 1;
    await route.fulfill(respond());
  });
  await page.route(`**/visits/${visitId}/transcript`, (route) =>
    route.fulfill({
      json: {
        visit_id: visitId,
        turns: [
          { turn_index: 1, question_id: "chief_concern", input_mode: "touch", text_original: "जोड़ों में दर्द", text_english: "Joint pain", stt_confidence: null },
          { turn_index: 2, question_id: "duration", input_mode: "touch", text_original: "1 से 3 दिन", text_english: "1 to 3 days", stt_confidence: null },
        ],
      },
    }),
  );
  return requests;
}

test("a doctor checks the AI draft against its sources, accepts it and records Ayurveda findings", async ({
  page,
}) => {
  const requests = await signedInDoctor(page);
  await page.goto(`/staff/consultations?visit_id=${visitId}&status=in_consult`);

  const ai = page.locator(".cx-card--ai").first();
  await expect(ai.getByText("Draft · not reviewed")).toBeVisible();
  // Each sentence cites the answers it rests on; one tap opens that answer.
  await ai.getByRole("button", { name: "Since when" }).click();
  await expect(page.getByRole("tab", { name: /^Interview transcript/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#turn-duration")).toHaveClass(/is-highlighted/);

  await page.getByRole("tab", { name: /^Overview/ }).click();
  await expect(page.getByText("Preferred not to say: tobacco and alcohol use")).toBeVisible();
  await ai.getByRole("button", { name: "Accept" }).click();
  await expect(ai.getByText("Accepted by Dr Rao")).toBeVisible();
  expect(requests[0]).toEqual({ path: "review", body: { decision: "accepted" }, ifMatch: '"3"' });

  await page.getByRole("tab", { name: /^Ayurveda/ }).click();
  await expect(page.getByText("Hard or constipated")).toBeVisible();
  await page.getByRole("textbox", { name: "Nadi (pulse)" }).fill("Vata predominant");
  await page.getByRole("button", { name: "Save examination" }).click();
  await expect(page.getByText("Examination saved.")).toBeVisible();
  expect(requests[1]).toEqual({
    path: "case-sheet",
    body: [{ op: "add", path: "/ayush/examination/nadi", value: "Vata predominant" }],
    ifMatch: '"4"',
  });

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);
  // The examination form fits a phone without sideways scrolling.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Save examination" })).toBeVisible();
  expect(
    await page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth),
  ).toBeLessThanOrEqual(0);
});
