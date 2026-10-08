import { expect, test, type Page } from "@playwright/test";
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
  hpi: { severity_0_10: 5, associated_symptoms: [] },
  past_history: { conditions: [] },
  medications: [],
  allergies: [],
  family_history: [],
  red_flags: [],
};
const durationQuestion = {
  id: "duration",
  input: "text",
  optional: true,
  multi: false,
  prompt: "Since when do you have this problem?",
  options: [],
};

async function seedSummary(page: Page) {
  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "browser", tts: "browser", ai: false } }),
  );
  await page.addInitScript((session) => {
    sessionStorage.setItem("osa.patient.session", JSON.stringify(session));
  }, patientSession);
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({
      json: {
        complete: true,
        case_sheet: caseSheet,
        readback: "Synthetic fixed-template read-back.",
        answered: [{ ...durationQuestion, answer: "two days" }],
      },
    }),
  );
}

test("a correction returns to the exact question and updates the read-back", async ({
  page,
}) => {
  await seedSummary(page);
  let correctionBody: Record<string, unknown> | undefined;
  await page.route("**/intake/sessions/*/interview/answers", async (route) => {
    expect(route.request().method()).toBe("PATCH");
    correctionBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: {
        complete: true,
        case_sheet: {
          ...caseSheet,
          red_flags: [{ id: "RF_CHEST_PAIN", level: "priority", source: "rule", evidence: "chest pain" }],
        },
        readback: "Updated synthetic read-back: three days.",
        answered: [{ ...durationQuestion, answer: "three days" }],
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
  await page.getByRole("button", { name: "Change: Since when do you have this problem?" }).click();
  await expect(page.getByRole("heading", { name: durationQuestion.prompt })).toBeVisible();
  const answer = page.getByRole("textbox");
  await answer.fill("three days");
  await page.getByRole("button", { name: /Save change|Next/ }).click();
  await expect(page.getByText("Updated synthetic read-back: three days.")).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Please tell the staff at the desk now." })).toBeVisible();
  expect(correctionBody).toMatchObject({ question_id: "duration", answer: "three days" });

  const screenshotDir = resolve(process.cwd(), "..", "..", "docs", "screenshots", "phase-10");
  mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({ path: resolve(screenshotDir, "s6-summary-after-correction.png"), fullPage: true });
  await page.getByRole("button", { name: "Yes, this is correct" }).click();
  await expect(page.getByText("SYN-010")).toBeVisible();
  expect(confirmCalls).toBe(1);
});

test("Listen reads the summary aloud with the browser voice", async ({ page }) => {
  await seedSummary(page);
  await page.addInitScript(() => {
    const spoken: Array<{ text: string; lang: string }> = [];
    Object.defineProperty(window, "__spokenPrompts", { value: spoken });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: {
        cancel() {},
        getVoices: () => [],
        speak(utterance: SpeechSynthesisUtterance) {
          spoken.push({ text: utterance.text, lang: utterance.lang });
        },
      },
    });
  });
  await page.goto("/kiosk/intake");
  await expect(page.getByText("Synthetic fixed-template read-back.")).toBeVisible();
  // Voice guidance may already be reading the summary; otherwise tap Listen.
  const listen = page.locator(".intake-readback").getByRole("button", { name: /Listen/ });
  if (await listen.isVisible()) await listen.click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as Window & { __spokenPrompts?: Array<{ text: string; lang: string }> })
            .__spokenPrompts,
      ),
    )
    .toContainEqual({ text: "Synthetic fixed-template read-back.", lang: "en-IN" });
});

test("a patient idle on the review screen for 90 seconds gets a nurse", async ({ page }) => {
  await page.clock.install();
  await seedSummary(page);
  let assistanceCalls = 0;
  await page.route("**/needs-assistance", async (route) => {
    assistanceCalls += 1;
    await route.fulfill({ json: { needs_assistance: true } });
  });
  await page.goto("/kiosk/intake");
  await expect(page.getByText("Synthetic fixed-template read-back.")).toBeVisible();
  await page.clock.fastForward(91_000);
  await expect(page.getByText("A nurse will help you")).toBeVisible();
  await expect.poll(() => assistanceCalls).toBe(1);
});
