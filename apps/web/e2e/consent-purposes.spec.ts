import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Consent by purpose (synthetic data only): the care consent first, then a separate and
// optional question about online AI help, which the patient can later stop from their
// status page.
const visitId = "00000000-0000-7000-8000-000000000c51";
const STATUS = "A".repeat(43);

async function seedConsentStep(page: Page) {
  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "off", tts: "browser", ai: false, online: false } }),
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
        ai_consent: {
          language: "en",
          version: "ai-consent-1.0",
          sha256: "b".repeat(64),
          text: "Synthetic notice about optional AI help.",
        },
        stage: "consent",
      }),
    );
  }, visitId);
  const answers: unknown[] = [];
  await page.route(`**/intake/sessions/${visitId}/consent`, (route) =>
    route.fulfill({ json: { granted: true, next: "identity" } }),
  );
  await page.route(`**/intake/sessions/${visitId}/consent/ai`, async (route) => {
    answers.push(route.request().postDataJSON());
    await route.fulfill({ json: { granted: false } });
  });
  return answers;
}

test("the patient is asked about AI help on its own and can say no", async ({ page }) => {
  const answers = await seedConsentStep(page);
  await page.goto("/kiosk/intake");
  await page.getByRole("button", { name: "I agree, continue" }).click();

  await expect(page.getByRole("heading", { name: "Help from AI (optional)" })).toBeVisible();
  await expect(page.getByText("Synthetic notice about optional AI help.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Yes, use AI help" })).toBeEnabled();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);

  await page.getByRole("button", { name: "No, continue without AI" }).click();
  await expect(page.getByLabel("How old are you?")).toBeVisible();
  expect(answers).toEqual([{ version: "ai-consent-1.0", sha256: "b".repeat(64), granted: false }]);
});

test("a patient stops AI help from their status page", async ({ page }) => {
  let aiHelp = "on";
  await page.route(`**/status/${STATUS}`, (route) =>
    route.fulfill({
      json: {
        state: "waiting",
        token: "MED-007",
        position: 3,
        eta_min: 10,
        eta_max: 20,
        department: { en: "General Medicine" },
        hospital: "Synthetic District Hospital",
        language: "en",
        ai_help: aiHelp,
      },
    }),
  );
  // No live stream in this test: the page falls back to polling.
  await page.route(`**/status/${STATUS}/stream`, (route) => route.abort());
  const withdrawals: string[] = [];
  await page.route(`**/status/${STATUS}/ai-consent/withdraw`, async (route) => {
    withdrawals.push(route.request().method());
    aiHelp = "withdrawn";
    await route.fulfill({ json: { ai_help: "withdrawn" } });
  });

  await page.goto(`/s/${STATUS}`);
  await expect(page.getByText("AI help is on for this visit.")).toBeVisible();
  await page.getByRole("button", { name: "Stop AI help" }).click();
  await expect(page.getByText(/Stop AI help for this visit\?/)).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);

  await page.getByRole("button", { name: "Yes, stop AI help" }).click();
  await expect(page.getByText("AI help is stopped for this visit.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop AI help" })).toHaveCount(0);
  expect(withdrawals).toEqual(["POST"]);
});

test("typing a phone number never ticks SMS; only the patient's tick sends it", async ({
  page,
}) => {
  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "off", tts: "browser", ai: false, online: false } }),
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
        sms_consent: {
          language: "en",
          version: "sms-consent-1.0",
          sha256: "c".repeat(64),
          text: "Synthetic notice about optional SMS.",
        },
        stage: "identity",
      }),
    );
  }, visitId);
  const identities: unknown[] = [];
  await page.route(`**/intake/sessions/${visitId}/identity`, async (route) => {
    identities.push(route.request().postDataJSON());
    await route.fulfill({ json: { next: "danger_signs" } });
  });

  await page.goto("/kiosk/intake");
  await page.getByLabel("How old are you?").fill("45");
  await page.getByRole("radio", { name: "Female" }).click();
  await page.getByRole("textbox", { name: /Mobile number/ }).fill("9876543210");
  const box = page.getByRole("checkbox", { name: "Synthetic notice about optional SMS." });
  await expect(box).toBeVisible();
  await expect(box).not.toBeChecked();
  await page.getByRole("button", { name: "Next" }).click();
  await expect.poll(() => identities.length).toBe(1);
  expect(identities[0]).toMatchObject({ sms_opt_in: false, sms_consent: null });
});

test("a patient stops SMS messages from their status page", async ({ page }) => {
  let sms = "on";
  await page.route(`**/status/${STATUS}`, (route) =>
    route.fulfill({
      json: {
        state: "waiting",
        token: "MED-008",
        position: 2,
        eta_min: 5,
        eta_max: 15,
        department: { en: "General Medicine" },
        hospital: "Synthetic District Hospital",
        language: "en",
        ai_help: "off",
        sms,
      },
    }),
  );
  await page.route(`**/status/${STATUS}/stream`, (route) => route.abort());
  const stops: string[] = [];
  await page.route(`**/status/${STATUS}/sms/stop`, async (route) => {
    stops.push(route.request().method());
    sms = "stopped";
    await route.fulfill({ json: { sms: "stopped" } });
  });

  await page.goto(`/s/${STATUS}`);
  await expect(page.getByText("Queue messages by SMS are on for this visit.")).toBeVisible();
  await page.getByRole("button", { name: "Stop SMS" }).click();
  await expect(page.getByText(/Stop queue messages by SMS\?/)).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);

  await page.getByRole("button", { name: "Yes, stop SMS" }).click();
  await expect(page.getByText("SMS messages are stopped for this visit.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop SMS" })).toHaveCount(0);
  expect(stops).toEqual(["POST"]);
});
