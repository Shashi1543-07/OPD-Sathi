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

test("fixed prompt audio plays from the service worker cache while offline", async ({
  page,
  context,
}) => {
  await page.addInitScript((session) => {
    sessionStorage.setItem("osa.patient.session", JSON.stringify(session));
  }, patientSession);
  const audioResponses: boolean[] = [];
  let failedRequests = 0;
  page.on("request", (request) => {
    if (request.url().includes("readback-audio")) failedRequests += 1;
  });
  page.on("requestfailed", () => {
    failedRequests += 1;
  });
  page.on("response", (response) => {
    if (response.url().includes("mock-tone.wav")) {
      audioResponses.push(response.fromServiceWorker());
    }
  });
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({
      json: {
        question: {
          id: "chief_concern",
          input: "text",
          optional: false,
          prompt: "What would you like to tell the doctor?",
        },
        answered: [],
      },
    }),
  );
  await page.goto("/kiosk/intake");
  await expect(page.getByText("What would you like to tell the doctor?")).toBeVisible();
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  // A first install controls the page after the next navigation.
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);
  await context.setOffline(true);
  await page.getByRole("button", { name: "Read aloud" }).click();
  await expect.poll(() => audioResponses.length).toBe(1);
  expect(audioResponses[0]).toBe(true);
  expect(failedRequests).toBe(0);
  expect(page.url()).toContain("/kiosk/intake");
});
