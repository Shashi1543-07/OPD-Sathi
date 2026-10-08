import { expect, test, type Page } from "@playwright/test";

// Chromium's fake microphone lets the real record -> upload -> transcribe path run.
test.use({
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  },
  permissions: ["microphone"],
});

const visitId = "00000000-0000-7000-8000-000000000801";
const patientSession = {
  visit_id: visitId,
  patient_access_token: "synthetic-patient-token",
  consent: {
    language: "en",
    version: "synthetic-consent-v1",
    sha256: "a".repeat(64),
    text: "Synthetic consent text.",
  },
  stage: "interview",
};
const chiefConcern = {
  id: "chief_concern",
  input: "text",
  optional: false,
  multi: true,
  prompt: "What is the main problem that brought you here today?",
  options: [{ id: "fever", label: "Fever", none: false }],
};

async function seedInterview(page: Page) {
  await page.addInitScript((session) => {
    sessionStorage.setItem("osa.patient.session", JSON.stringify(session));
  }, patientSession);
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "sarvam", tts: "browser", ai: true } }),
  );
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({ json: { question: chiefConcern, answered: [] } }),
  );
}

test("records an answer, shows the transcript, and submits the edited text as voice", async ({
  page,
}) => {
  await seedInterview(page);
  let uploadedBytes = 0;
  await page.route(`**/intake/sessions/${visitId}/transcribe?*`, async (route) => {
    uploadedBytes = route.request().postDataBuffer()?.length ?? 0;
    await route.fulfill({ json: { text: "Headache since this morning" } });
  });
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/intake/sessions/*/interview/answers", async (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { question: chiefConcern, answered: [] } });
  });

  await page.goto("/kiosk/intake");
  await expect(page.getByRole("heading", { name: chiefConcern.prompt })).toBeVisible();
  await page.getByRole("button", { name: /Tap and speak/ }).click();
  await expect(page.getByText("Listening… speak now")).toBeVisible();
  // Let the fake microphone produce a few audio chunks before stopping.
  await page.waitForTimeout(1500);
  const stop = page.getByRole("button", { name: /Tap when done/ });
  if (await stop.isVisible()) await stop.click();

  const answer = page.getByRole("textbox");
  await expect(answer).toHaveValue("Headache since this morning");
  await expect(page.getByText(/We heard you/)).toBeVisible();
  expect(uploadedBytes).toBeGreaterThan(0);

  await answer.fill("Headache since morning");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect.poll(() => submitted?.answer).toBe("Headache since morning");
  expect(submitted?.input_mode).toBe("voice");
});

test("a denied microphone leaves tapping and typing available", async ({ page }) => {
  await seedInterview(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          throw new DOMException("Permission denied", "NotAllowedError");
        },
      },
    });
  });
  await page.goto("/kiosk/intake");
  await page.getByRole("button", { name: /Tap and speak/ }).click();
  await expect(
    page.getByText("Microphone permission was denied. Please tap or type your answer."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Fever" })).toBeEnabled();
});
