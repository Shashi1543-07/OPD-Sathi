import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Old papers (synthetic data only): the patient photographs an old test report on the review
// screen, and the doctor sees the photo beside what AI read from it, compares the two and
// decides. Results are flagged only against the range printed on the same paper.
const visitId = "00000000-0000-7000-8000-00000000d0c1";
// A 1x1 PNG: the kiosk turns it into a small JPEG before sending it.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);
const PAPERS = /\/intake\/sessions\/[^/]+\/documents(?:\?.*)?$/;
const ONE_PAPER = /\/intake\/sessions\/[^/]+\/documents\/[^/?]+$/;

async function seedReviewScreen(page: Page) {
  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "off", tts: "browser", ai: true, online: true } }),
  );
  await page.addInitScript((id) => {
    sessionStorage.setItem(
      "osa.patient.session",
      JSON.stringify({
        visit_id: id,
        patient_access_token: "synthetic-patient-token",
        consent: {
          language: "en",
          version: "consent-1.0",
          sha256: "a".repeat(64),
          text: "Synthetic consent text.",
        },
        stage: "summary",
      }),
    );
  }, visitId);
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({
      json: {
        complete: true,
        case_sheet: {
          schema_version: "1.0",
          language: "en",
          chief_complaint: { text_original: "tiredness", text_en: "tiredness", confidence: 0.9 },
          hpi: { associated_symptoms: [] },
          past_history: { conditions: [] },
          medications: [],
          allergies: [],
          family_history: [],
          red_flags: [],
        },
        readback: "Synthetic read-back: tiredness for two weeks.",
        answered: [
          {
            id: "chief_concern",
            input: "text",
            optional: false,
            multi: false,
            prompt: "What is the main problem today?",
            options: [],
            answer: "tiredness",
          },
        ],
      },
    }),
  );
  const uploads: { kind: string | null; type: string; bytes: number }[] = [];
  const removed: string[] = [];
  await page.route(PAPERS, async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const kind = new URL(request.url()).searchParams.get("kind");
      uploads.push({
        kind,
        type: request.headers()["content-type"] ?? "",
        bytes: request.postDataBuffer()?.length ?? 0,
      });
      await route.fulfill({
        status: 201,
        json: {
          id: `paper-${uploads.length}`,
          kind,
          size: request.postDataBuffer()?.length ?? 0,
          added_at: "2026-10-17T04:30:00Z",
        },
      });
      return;
    }
    await route.fulfill({ json: { documents: [], limit: 5 } });
  });
  await page.route(ONE_PAPER, async (route) => {
    removed.push(new URL(route.request().url()).pathname.split("/").pop() ?? "");
    await route.fulfill({ status: 204, body: "" });
  });
  return { uploads, removed };
}

test("a patient adds a photo of an old test report before confirming", async ({ page }) => {
  const { uploads, removed } = await seedReviewScreen(page);
  await page.goto("/kiosk/intake");
  await expect(page.getByText("Synthetic read-back: tiredness for two weeks.")).toBeVisible();
  const papers = page.getByRole("region", { name: "Old papers (optional)" });
  await expect(
    papers.getByText(/AI will read them for the doctor, who checks each one against the photo\./),
  ).toBeVisible();

  const chooser = page.waitForEvent("filechooser");
  await papers.getByRole("button", { name: "Test report" }).click();
  await (await chooser).setFiles({ name: "report.png", mimeType: "image/png", buffer: PNG });
  await expect(papers.getByRole("img", { name: "Photo 1: Test report" })).toBeVisible();
  await expect(papers.getByText("Photo saved for the doctor.")).toBeVisible();
  expect(uploads).toHaveLength(1);
  expect(uploads[0].kind).toBe("lab_report");
  // Sent as a small JPEG, whatever the camera produced.
  expect(uploads[0].type).toBe("image/jpeg");
  expect(uploads[0].bytes).toBeGreaterThan(0);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
  // The section fits a phone without sideways scrolling.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(papers.getByRole("button", { name: "Remove photo 1: Test report" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);

  await papers.getByRole("button", { name: "Remove photo 1: Test report" }).click();
  await expect(papers.getByText("Photo removed.")).toBeVisible();
  await expect(papers.getByRole("img")).toHaveCount(0);
  expect(removed).toEqual(["paper-1"]);
});

function paper(reviewStatus: "unreviewed" | "accepted") {
  return {
    id: "paper-1",
    visit_id: visitId,
    service_date: "2026-10-17",
    kind: "lab_report",
    source: "patient",
    added_by: null,
    added_at: "2026-10-17T04:30:00Z",
    size: PNG.length,
    document_date: "2026-09-30",
    reading_status: "complete",
    reading: {
      readable: true,
      document_type: "lab_report",
      document_date: "2026-09-30",
      facility: "Synthetic Diagnostics",
      diagnoses: [],
      medicines: [],
      lab_results: [
        {
          test: "Haemoglobin",
          value: "9.8",
          unit: "g/dL",
          reference_range: "12.0 - 15.0",
          printed_flag: "low",
          range_flag: "below",
        },
        {
          test: "Fasting glucose",
          value: "92",
          unit: "mg/dL",
          reference_range: "70 - 100",
          printed_flag: null,
          range_flag: "within",
        },
      ],
      procedures: [],
    },
    ai_withdrawn: false,
    review_status: reviewStatus,
    reviewed_by: reviewStatus === "accepted" ? "Dr Rao" : null,
    reviewed_at: reviewStatus === "accepted" ? "2026-10-17T05:00:00Z" : null,
  };
}

async function signedInDoctor(page: Page) {
  let reviewStatus: "unreviewed" | "accepted" = "unreviewed";
  const reviews: unknown[] = [];
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "doctor-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-00000000d0c3",
          name: "Dr Rao",
          email: "doctor@example.test",
          role: "doctor",
          hospital_id: "00000000-0000-7000-8000-00000000d0c0",
        },
      },
    }),
  );
  await page.route(/\/queue(?:\?.*)?$/, (route) =>
    route.fulfill({ json: { items: [], departments: [], next_cursor: null, limit: 50, missed: [] } }),
  );
  await page.route(`**/visits/${visitId}/case-sheet`, (route) =>
    route.fulfill({
      json: {
        visit_id: visitId,
        version: 2,
        case_sheet: {
          schema_version: "1.0",
          language: "en",
          chief_complaint: { text_original: "tiredness", text_en: "Tiredness", confidence: 0.9 },
          hpi: { associated_symptoms: [], pertinent_negatives: [] },
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
          field_meta: {},
          disclaimer: "History only. Not a diagnosis.",
        },
        confirmed_by_patient: true,
        confirmed_at: "2026-10-17T04:35:00Z",
        patient: { token: "MED-031", age_years: 34, sex: "female", language: "en", urgency: "routine", ai_help: "on" },
      },
      headers: { ETag: '"2"' },
    }),
  );
  await page.route(`**/visits/${visitId}/transcript`, (route) =>
    route.fulfill({ json: { visit_id: visitId, turns: [] } }),
  );
  await page.route(`**/visits/${visitId}/documents`, (route) =>
    route.fulfill({
      json: {
        visit_id: visitId,
        documents: [paper(reviewStatus)],
        earlier: [],
        earlier_shown: true,
        limit: 10,
      },
    }),
  );
  await page.route(`**/visits/${visitId}/documents/paper-1/content`, (route) =>
    route.fulfill({ body: PNG, contentType: "image/png", headers: { "Cache-Control": "private, no-store" } }),
  );
  await page.route(`**/visits/${visitId}/documents/paper-1/review`, async (route) => {
    reviews.push(route.request().postDataJSON());
    reviewStatus = "accepted";
    await route.fulfill({ json: paper(reviewStatus) });
  });
  return reviews;
}

test("a doctor compares what AI read with the photo and accepts it", async ({ page }) => {
  const reviews = await signedInDoctor(page);
  await page.goto(`/staff/consultations?visit_id=${visitId}&status=in_consult`);

  const tab = page.getByRole("tab", { name: /^Old papers/ });
  await expect(tab).toContainText("1");
  await tab.click();
  await expect(page.getByRole("img", { name: "Photo of the paper: Test report" })).toBeVisible();
  await expect(page.getByText("AI reading · compare with the photo")).toBeVisible();
  const table = page.getByRole("table");
  await expect(table.getByRole("row", { name: /Haemoglobin/ })).toContainText(
    "Below the printed range · Marked low on the report",
  );
  await expect(table.getByRole("row", { name: /Fasting glucose/ })).toContainText(
    "Within the printed range",
  );
  // The photo opens larger for a closer look.
  await page.getByRole("button", { name: "Enlarge the photo: Test report" }).click();
  await expect(page.getByRole("button", { name: "Make the photo smaller: Test report" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );

  await page.getByRole("button", { name: "Matches the photo" }).click();
  await expect(page.getByText("Matches the photo · checked by Dr Rao")).toBeVisible();
  expect(reviews).toEqual([{ decision: "accepted" }]);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("table")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
});
