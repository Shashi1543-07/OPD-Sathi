import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Locale = "en" | "hi" | "mr";

function copyFor(locale: Locale) {
  const read = (namespace: string) =>
    JSON.parse(
      readFileSync(resolve(process.cwd(), "src", "locales", locale, `${namespace}.json`), "utf8"),
    ) as Record<string, string>;
  return { intake: read("intake"), kiosk: read("kiosk") };
}

const languageButtons: Record<Locale, string> = {
  en: "English",
  hi: "हिन्दी",
  mr: "मराठी",
};

const sampleQuestions: Record<Locale, { chief: string; fever: string; duration: string; days: string }> = {
  en: { chief: "What is the main problem?", fever: "Fever", duration: "Since when?", days: "1 to 3 days" },
  hi: { chief: "मुख्य परेशानी क्या है?", fever: "बुखार", duration: "कब से?", days: "1 से 3 दिन" },
  mr: { chief: "मुख्य त्रास काय आहे?", fever: "ताप", duration: "केव्हापासून?", days: "1 ते 3 दिवस" },
};

async function installApiMocks(page: Page, locale: Locale) {
  const sample = sampleQuestions[locale];
  const questions = [
    {
      id: "chief_concern",
      prompt: sample.chief,
      input: "text",
      optional: false,
      multi: true,
      options: [{ id: "fever", label: sample.fever, none: false }],
    },
    {
      id: "duration",
      prompt: sample.duration,
      input: "text",
      optional: true,
      multi: false,
      options: [{ id: "days_1_3", label: sample.days, none: false }],
    },
    { id: "severity", prompt: "0 – 10", input: "scale", optional: true, multi: false, options: [] },
  ];
  const answered: { id: string; prompt: string; answer: string | number | null }[] = [];
  let questionIndex = 0;
  const caseSheet = () => ({
    language: locale,
    chief_complaint: { text_original: sample.fever, text_en: "Fever" },
    hpi: {},
    past_history: { conditions: [] },
    medications: [],
    allergies: [],
    family_history: [],
    red_flags: [{ id: "RF_DS_TILE", level: "urgent" }],
  });
  const current = () =>
    questionIndex < questions.length
      ? {
          complete: false,
          question: questions[questionIndex],
          progress: { current: questionIndex + 1, total: questions.length },
          answered: [...answered],
        }
      : {
          complete: true,
          readback: "Please review what you told us.",
          answered: [...answered],
          case_sheet: caseSheet(),
        };

  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "browser", tts: "browser", ai: false } }),
  );
  await page.route("**/kiosks/activate", (route) =>
    route.fulfill({ json: { access_token: "kiosk-test-token" } }),
  );
  await page.route("**/intake/sessions", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 201,
      json: {
        visit_id: "00000000-0000-7000-8000-000000000001",
        patient_access_token: "patient-test-token",
        consent: {
          language: locale,
          version: "consent-test",
          sha256: "a".repeat(64),
          text: "We ask about your health to help with your visit. This is not a diagnosis.",
        },
      },
    });
  });
  await page.route("**/intake/sessions/*/consent", (route) =>
    route.fulfill({ json: { granted: true, next: "identity" } }),
  );
  await page.route("**/intake/sessions/*/identity", (route) =>
    route.fulfill({ json: { next: "danger_signs" } }),
  );
  await page.route("**/intake/sessions/*/danger-tiles", (route) =>
    route.fulfill({ json: { tiles: [{ id: "chest_pain", label: "Chest pain / छाती" }] } }),
  );
  await page.route("**/intake/sessions/*/danger-signs", async (route) => {
    expect(JSON.parse(route.request().postData() ?? "{}").tile_ids).toContain("chest_pain");
    await route.fulfill({ json: { staff_help_required: true } });
  });
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({ json: current() }),
  );
  await page.route("**/intake/sessions/*/interview/answers", async (route) => {
    const payload = JSON.parse(route.request().postData() ?? "{}");
    const question = questions[questionIndex];
    answered.push({
      id: question.id,
      prompt: question.prompt,
      answer: payload.skip ? null : payload.answer,
    });
    questionIndex += 1;
    await route.fulfill({ json: current() });
  });
  await page.route("**/intake/sessions/*/confirm", (route) =>
    route.fulfill({
      json: {
        confirmed: true,
        status: "queued",
        token: "GEN-007",
        status_token: "S".repeat(43),
        status_url: `/s/${"S".repeat(43)}`,
        position: 1,
        department_name: "General Medicine",
        wait_minutes: 10,
      },
    }),
  );
}

for (const language of ["en", "hi", "mr"] as const) {
  test(`Phase 4 check-in completes in ${language}`, async ({ page }) => {
    const { intake, kiosk } = copyFor(language);
    const sample = sampleQuestions[language];
    await installApiMocks(page, language);
    await page.goto("/kiosk");
    await page.getByRole("button", { name: languageButtons[language] }).first().click();
    await page.getByLabel(kiosk.activationCode).fill("A".repeat(40));
    await page.getByRole("button", { name: kiosk.activate }).click();
    await page.getByRole("button", { name: kiosk.startIntake }).click();

    await page.getByRole("button", { name: intake.consentAgree }).click();
    await page.getByLabel(intake.ageQuestion).fill("34");
    await page.getByRole("radio", { name: intake.sexFemale }).click();
    await page.getByRole("button", { name: intake.next, exact: true }).click();
    await page.getByRole("button", { name: intake.returningNo }).click();

    await page.getByRole("heading", { name: intake.dangerTitle }).waitFor();
    await page.getByRole("button", { name: "Chest pain / छाती" }).click();
    await page.getByRole("button", { name: intake.dangerContinue }).click();

    await expect(page.getByRole("heading", { name: sample.chief })).toBeVisible();
    await page.getByRole("button", { name: sample.fever }).click();
    await page.getByRole("button", { name: intake.next, exact: true }).click();
    await expect(page.getByRole("heading", { name: sample.duration })).toBeVisible();
    await page.getByRole("button", { name: intake.skip, exact: true }).click();
    await page.getByRole("radio", { name: "7", exact: true }).click();
    await page.getByRole("button", { name: intake.next, exact: true }).click();

    await expect(page.getByRole("heading", { name: intake.reviewTitle })).toBeVisible();
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(
      accessibility.violations.filter((item) =>
        ["critical", "serious"].includes(item.impact ?? ""),
      ),
    ).toEqual([]);
    if (language === "en") {
      const screenshotDirectory = resolve(process.cwd(), "..", "..", "docs", "screenshots", "phase-04");
      mkdirSync(screenshotDirectory, { recursive: true });
      await page.screenshot({ path: resolve(screenshotDirectory, "summary-en.png"), fullPage: true });
    }
    await page.getByRole("button", { name: intake.confirm }).click();
    await expect(page.getByRole("heading", { name: intake.doneTitle })).toBeVisible();
    await expect(page.getByText("GEN-007")).toBeVisible();
  });
}
