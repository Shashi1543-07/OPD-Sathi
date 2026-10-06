import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const visitId = "00000000-0000-7000-8000-000000000a10";
const patientSession = {
  visit_id: visitId,
  patient_access_token: "synthetic-patient-token",
  consent: {
    language: "en",
    version: "synthetic-consent-v1",
    sha256: "a".repeat(64),
    text: "Synthetic consent text.",
  },
  stage: "summary",
};
const caseSheet = {
  schema_version: "1.0",
  language: "en",
  chief_complaint: {
    text_original: "sharp synthetic pain",
    text_en: "sharp synthetic pain",
    confidence: 0.9,
  },
  hpi: {
    onset: {
      text_original: "two days",
      text_en: "two days",
      confidence: 0.9,
    },
    severity_0_10: 5,
    associated_symptoms: [],
  },
  past_history: { conditions: [] },
  medications: [],
  allergies: [],
  family_history: [],
  red_flags: [],
  routing: {
    department_code: "GENMED",
    confidence: 0.9,
    reason_en: "Synthetic routing fixture.",
    needs_nurse_confirmation: false,
  },
};

async function seedSummary(page: import("@playwright/test").Page) {
  await page.addInitScript((session) => {
    sessionStorage.setItem("osa.patient.session", JSON.stringify(session));
  }, patientSession);
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({
      json: {
        complete: true,
        case_sheet: caseSheet,
        readback: "Synthetic fixed-template read-back.",
        answered: [
          {
            id: "duration",
            input: "text",
            optional: true,
            prompt: "How long has this been happening?",
            answer: "two days",
          },
        ],
      },
    }),
  );
}

test("correction returns to the exact question and updates the read-back", async ({
  page,
}) => {
  await seedSummary(page);
  let correctionBody: Record<string, unknown> | undefined;
  await page.route("**/intake/sessions/*/interview/answers", async (route) => {
    correctionBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: {
        complete: true,
        case_sheet: {
          ...caseSheet,
          hpi: { ...caseSheet.hpi, onset: { text_original: "three days" } },
          red_flags: [
            { id: "DS_CHEST", level: "urgent", source: "rule", evidence: "chest pain" },
          ],
        },
        readback: "Updated synthetic read-back: three days.",
        answered: [
          {
            id: "duration",
            input: "text",
            optional: true,
            prompt: "How long has this been happening?",
            answer: "three days",
          },
        ],
      },
    });
  });
  let confirmCalls = 0;
  await page.route("**/intake/sessions/*/confirm", async (route) => {
    confirmCalls += 1;
    await route.fulfill({
      json: {
        confirmed: true,
        token: "SYN-010",
        status_token: "synthetic-status-token",
        status_url: "/s/synthetic-status-token",
        position: 1,
      },
    });
  });

  await page.goto("/kiosk/intake");
  await expect(page.getByText("Synthetic fixed-template read-back.")).toBeVisible();
  await page.getByRole("button", { name: "Change: How long has this been happening?" }).click();
  const answer = page.getByRole("textbox", { name: "Type your answer" });
  await expect(answer).toBeVisible();
  await answer.fill("three days");
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Updated synthetic read-back: three days.")).toBeVisible();
  await expect(page.getByText("Please inform the staff at the desk now.")).toBeVisible();
  expect(correctionBody).toMatchObject({ question_id: "duration", answer: "three days" });
  const screenshotDir = resolve(process.cwd(), "..", "..", "docs", "screenshots", "phase-10");
  mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: resolve(screenshotDir, "s6-summary-after-correction.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "This is correct" }).click();
  await expect(page.getByText("SYN-010")).toBeVisible();
  expect(confirmCalls).toBe(1);
});

test("TTS failure leaves read-back text available without an error message", async ({
  page,
}) => {
  await seedSummary(page);
  let audioRequests = 0;
  await page.route("**/readback-audio", async (route) => {
    audioRequests += 1;
    await route.fulfill({ status: 503, json: { detail: "synthetic provider failure" } });
  });
  await page.goto("/kiosk/intake");
  await expect(page.getByText("Synthetic fixed-template read-back.")).toBeVisible();
  await page.getByRole("button", { name: "Read aloud" }).click();
  await expect.poll(() => audioRequests).toBe(1);
  await expect(page.getByText("Synthetic fixed-template read-back.")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("summary idle timeout flags nurse assistance after 90 seconds", async ({
  page,
}) => {
  await page.clock.install();
  await seedSummary(page);
  let assistanceCalls = 0;
  await page.route("**/needs-assistance", async (route) => {
    assistanceCalls += 1;
    await route.fulfill({ json: { needs_assistance: true } });
  });
  await page.goto("/kiosk/intake");
  await expect(page.getByText("Synthetic fixed-template read-back.")).toBeVisible();
  await page.clock.fastForward(90_000);
  await expect(page.getByRole("status")).toHaveText("A nurse will help you");
  expect(assistanceCalls).toBe(1);
});
