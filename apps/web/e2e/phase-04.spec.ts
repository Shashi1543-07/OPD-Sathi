import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const locales = {
  en: {
    languageButton: "English",
    activationLabel: "One-time activation code",
    activate: "Activate kiosk",
    start: "Start patient check-in",
    agree: "I agree and want to continue",
    dangerTitle: "Do you need help right now?",
    tile: "Chest discomfort",
    next: "Next",
    chiefPrompt: "What would you like the doctor to know?",
    durationPrompt: "How long have you noticed this concern?",
    summaryTitle: "Please check what you told us",
    confirm: "This is correct",
  },
  hi: {
    languageButton: "हिन्दी",
    activationLabel: "एक बार का सक्रियण कोड",
    activate: "कियोस्क सक्रिय करें",
    start: "मरीज़ का चेक-इन शुरू करें",
    agree: "मैं सहमत हूँ और आगे बढ़ना चाहता/चाहती हूँ",
    dangerTitle: "क्या आपको अभी मदद चाहिए?",
    tile: "सीने में तकलीफ़",
    next: "अगला",
    chiefPrompt: "आप क्या बताना चाहेंगे?",
    durationPrompt: "आपको यह परेशानी कब से है?",
    summaryTitle: "कृपया अपने जवाब जाँचें",
    confirm: "यह सही है",
  },
  mr: {
    languageButton: "मराठी",
    activationLabel: "एकदाच वापरायचा सक्रियकरण कोड",
    activate: "किऑस्क सक्रिय करा",
    start: "रुग्णाची नोंदणी सुरू करा",
    agree: "माझी संमती आहे, पुढे सुरू ठेवा",
    dangerTitle: "तुम्हाला आत्ता मदत हवी आहे का?",
    tile: "छातीत त्रास",
    next: "पुढे",
    chiefPrompt: "तुम्हाला काय सांगायचे आहे?",
    durationPrompt: "तुम्हाला हा त्रास किती काळापासून आहे?",
    summaryTitle: "तुम्ही दिलेली माहिती तपासा",
    confirm: "हे बरोबर आहे",
  },
} as const;

type Locale = keyof typeof locales;

async function installApiMocks(page: Page, locale: Locale) {
  const copy = locales[locale];
  const answered: { id: string; prompt: string; answer: string | number }[] = [];
  let questionIndex = 0;
  const questions = [
    { id: "chief_concern", prompt: copy.chiefPrompt, input: "text", optional: false },
    { id: "duration", prompt: copy.durationPrompt, input: "text", optional: true },
    { id: "severity", prompt: "0 to 10", input: "scale", optional: true },
  ];
  const questionResponse = () => ({
    complete: false,
    question: questions[questionIndex],
    answered: [...answered],
  });
  await page.route("**/kiosks/activate", async (route) =>
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
  await page.route("**/intake/sessions/*/consent", async (route) =>
    route.fulfill({ json: { granted: true, next: "identity" } }),
  );
  await page.route("**/intake/sessions/*/identity", async (route) =>
    route.fulfill({ json: { next: "danger_signs" } }),
  );
  await page.route("**/intake/sessions/*/danger-tiles", async (route) =>
    route.fulfill({ json: { tiles: [{ id: "chest_pain", label: copy.tile }] } }),
  );
  await page.route("**/intake/sessions/*/danger-signs", async (route) => {
    expect(JSON.parse(route.request().postData() ?? "{}").tile_ids).toContain("chest_pain");
    await route.fulfill({ json: { staff_help_required: true } });
  });
  await page.route("**/intake/sessions/*/interview/next-question", async (route) => {
    await route.fulfill({ json: questionIndex < questions.length ? questionResponse() : {
      complete: true,
      readback: "Please review what you told us.",
      answered: [...answered],
      case_sheet: {
        language: locale,
        chief_complaint: { text_original: "fever", text_en: "fever" },
        hpi: {},
        past_history: { conditions: [] },
        medications: [],
        allergies: [],
        family_history: [],
        red_flags: [{ id: "CHEST_PAIN", level: "urgent" }],
      },
    } });
  });
  await page.route("**/intake/sessions/*/interview/answers", async (route) => {
    const payload = JSON.parse(route.request().postData() ?? "{}");
    const question = questions[questionIndex];
    if (route.request().method() === "PATCH") {
      const previous = answered.find((item) => item.id === payload.question_id);
      if (previous) previous.answer = payload.answer;
      return route.fulfill({ json: questionResponse() });
    }
    if (!payload.skip) answered.push({ id: question.id, prompt: question.prompt, answer: payload.answer });
    questionIndex += 1;
    const complete = questionIndex >= questions.length;
    await route.fulfill({ json: complete ? {
      complete: true,
      readback: "Please review what you told us.",
      answered: [...answered],
      case_sheet: {
        language: locale,
        chief_complaint: { text_original: String(answered[0]?.answer ?? "fever"), text_en: "fever" },
        hpi: {}, past_history: { conditions: [] }, medications: [], allergies: [], family_history: [],
        red_flags: [{ id: "CHEST_PAIN", level: "urgent" }],
      },
    } : questionResponse() });
  });
  await page.route("**/intake/sessions/*/confirm", async (route) =>
    route.fulfill({ json: { confirmed: true, token: "Will be assigned when queue registration is available." } }),
  );
}

for (const language of ["en", "hi", "mr"] as const) {
  test(`Phase 4 check-in completes in ${language}`, async ({ page }) => {
    const copy = locales[language];
    await installApiMocks(page, language);
    await page.goto("/kiosk");
    await page.getByRole("button", { name: copy.languageButton }).first().click();
    await page.getByLabel(copy.activationLabel).fill("A".repeat(40));
    await page.getByRole("button", { name: copy.activate }).click();
    await page.getByRole("button", { name: copy.start }).click();
    await page.getByRole("button", { name: copy.agree }).first().click();
    await page.getByLabel(/Age in years|उम्र|वय/).fill("34");
    await page.getByRole("button", { name: /Continue|आगे बढ़ें|पुढे सुरू ठेवा/ }).click();
    await page.getByRole("heading", { name: copy.dangerTitle }).waitFor();
    await page.getByRole("button", { name: copy.tile }).click();
    await page.getByRole("button", { name: copy.next }).click();
    await page.getByPlaceholder(/Type your answer|अपना जवाब|तुमचे उत्तर/).fill("fever");
    await page.getByRole("button", { name: copy.next }).click();
    await expect(page.getByText(copy.durationPrompt)).toBeVisible();
    await page.getByRole("button", { name: /Skip this question|यह सवाल छोड़ें|हा प्रश्न वगळा/ }).click();
    await page.getByRole("button", { name: /Change previous answer|पिछला जवाब बदलें|मागील उत्तर बदला/ }).click();
    await page.getByPlaceholder(/Type your answer|अपना जवाब|तुमचे उत्तर/).fill("fever and cough");
    await page.getByRole("button", { name: copy.next }).click();
    await page.getByRole("button", { name: "7" }).click();
    await page.getByRole("button", { name: copy.next }).click();
    await expect(page.getByRole("heading", { name: copy.summaryTitle })).toBeVisible();
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
    if (language === "en") {
      const screenshotDirectory = resolve(process.cwd(), "..", "..", "docs", "screenshots", "phase-04");
      mkdirSync(screenshotDirectory, { recursive: true });
      await page.screenshot({ path: resolve(screenshotDirectory, "summary-en.png"), fullPage: true });
    }
    await page.getByRole("button", { name: copy.confirm }).click();
    await expect(page.getByRole("heading", { name: /Your information is ready|आपकी जानकारी|तुमची माहिती/ })).toBeVisible();
  });
}
