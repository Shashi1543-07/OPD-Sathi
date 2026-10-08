import { expect, test } from "@playwright/test";

const patientSession = {
  visit_id: "00000000-0000-7000-8000-000000000a10",
  patient_access_token: "synthetic-patient-token",
  consent: {
    language: "en",
    version: "synthetic-consent-v1",
    sha256: "a".repeat(64),
    text: "Synthetic consent text.",
  },
  stage: "interview",
};
const question = {
  id: "chief_concern",
  input: "text",
  optional: false,
  multi: true,
  prompt: "What is the main problem that brought you here today?",
  options: [{ id: "fever", label: "Fever", none: false }],
};

test("the cached kiosk shell, browser voice and offline form work without a network", async ({
  page,
  context,
}) => {
  await page.addInitScript((session) => {
    sessionStorage.setItem("osa.patient.session", JSON.stringify(session));
    const spoken: string[] = [];
    Object.defineProperty(window, "__spoken", { value: spoken });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: {
        cancel() {},
        getVoices: () => [],
        speak(utterance: SpeechSynthesisUtterance) {
          spoken.push(utterance.text);
        },
      },
    });
  }, patientSession);
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "browser", tts: "browser", ai: false } }),
  );
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({ json: { question, answered: [] } }),
  );
  await page.goto("/kiosk/intake");
  await expect(page.getByRole("heading", { name: question.prompt })).toBeVisible();
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  // A first install controls the page after the next navigation.
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);

  await context.setOffline(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: question.prompt })).toBeVisible();
  await page.getByRole("button", { name: /Listen/ }).first().click();
  await expect
    .poll(() => page.evaluate(() => (window as Window & { __spoken?: string[] }).__spoken))
    .toContain(question.prompt);

  await page.goto("/kiosk/offline-intake");
  await expect(page.getByRole("heading", { name: "Offline check-in" })).toBeVisible();
});
