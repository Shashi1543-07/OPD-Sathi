import { expect, test, type Page } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-000000000b01";
const chiefConcern = {
  id: "chief_concern",
  input: "text",
  optional: false,
  multi: true,
  prompt: "What is the main problem that brought you here today?",
  options: [
    { id: "fever", label: "Fever", none: false, excludes: [] },
    { id: "cough", label: "Cough", none: false, excludes: [] },
  ],
};
const duration = {
  id: "duration",
  input: "text",
  optional: true,
  multi: false,
  prompt: "Since when do you have this problem?",
  options: [{ id: "today", label: "Since today", none: false, excludes: [] }],
};
const feverPattern = {
  id: "fever",
  input: "text",
  optional: true,
  multi: true,
  prompt: "How is the fever?",
  options: [
    { id: "high", label: "Very high fever", none: false, excludes: [] },
    { id: "comes_goes", label: "Comes and goes", none: false, excludes: ["continuous"] },
    { id: "continuous", label: "All the time", none: false, excludes: ["comes_goes"] },
  ],
};

async function seedInterview(page: Page, current: object, answered: object[]) {
  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "browser", tts: "browser", ai: false } }),
  );
  await page.addInitScript((id) => {
    sessionStorage.setItem(
      "osa.patient.session",
      JSON.stringify({
        visit_id: id,
        patient_access_token: "synthetic-patient-token",
        consent: {
          language: "en",
          version: "synthetic-consent-v1",
          sha256: "a".repeat(64),
          text: "Synthetic consent text.",
        },
        stage: "interview",
      }),
    );
  }, visitId);
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({
      json: { complete: false, question: current, progress: { current: 2, total: 10 }, answered },
    }),
  );
}

test("Back re-asks the previous question with its answer filled in, then returns to safety", async ({
  page,
}) => {
  await seedInterview(page, duration, [{ ...chiefConcern, answer: "Fever" }]);
  let backCalls = 0;
  await page.route("**/intake/sessions/*/interview/back", async (route) => {
    backCalls += 1;
    expect(route.request().method()).toBe("POST");
    await route.fulfill({
      json: {
        complete: false,
        question: chiefConcern,
        progress: { current: 1, total: 10 },
        answered: [],
        draft: "Fever",
      },
    });
  });
  await page.route("**/intake/sessions/*/danger-tiles", (route) =>
    route.fulfill({ json: { tiles: [{ id: "chest_pain", label: "Chest discomfort" }] } }),
  );

  await page.goto("/kiosk/intake");
  await expect(page.getByRole("heading", { name: duration.prompt })).toBeVisible();
  await page.getByRole("button", { name: "Back", exact: true }).click();

  await expect(page.getByRole("heading", { name: chiefConcern.prompt })).toBeVisible();
  await expect(page.getByRole("button", { name: "Fever" })).toHaveAttribute("aria-pressed", "true");
  expect(backCalls).toBe(1);

  // Nothing answered yet: Back now returns to the safety screen instead of the server.
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Do you have any of these right now?" })).toBeVisible();
  expect(backCalls).toBe(1);
  // ...and from there to the details screen.
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page.getByRole("radiogroup")).toBeVisible();
});

test("choices that contradict each other cannot both be picked", async ({ page }) => {
  await seedInterview(page, feverPattern, [{ ...chiefConcern, answer: "Fever" }]);
  await page.goto("/kiosk/intake");
  const comesAndGoes = page.getByRole("button", { name: "Comes and goes" });
  const allTheTime = page.getByRole("button", { name: "All the time" });
  const veryHigh = page.getByRole("button", { name: "Very high fever" });

  await veryHigh.click();
  await comesAndGoes.click();
  await allTheTime.click();
  await expect(allTheTime).toHaveAttribute("aria-pressed", "true");
  await expect(comesAndGoes).toHaveAttribute("aria-pressed", "false");
  await expect(veryHigh).toHaveAttribute("aria-pressed", "true");
});

test("Stop and exit cancels the visit after a confirmation and returns to the start", async ({
  page,
}) => {
  await seedInterview(page, duration, [{ ...chiefConcern, answer: "Fever" }]);
  const cancelled: string[] = [];
  await page.route(`**/intake/sessions/${visitId}`, async (route) => {
    cancelled.push(route.request().method());
    await route.fulfill({ status: 204, body: "" });
  });

  await page.goto("/kiosk/intake");
  await page.getByRole("button", { name: "Stop and exit" }).click();
  const dialog = page.getByRole("dialog", { name: "Stop registration?" });
  await expect(dialog).toBeVisible();

  // "No" keeps the patient exactly where they were.
  await dialog.getByRole("button", { name: "No, continue" }).click();
  await expect(page.getByRole("heading", { name: duration.prompt })).toBeVisible();
  expect(cancelled).toEqual([]);

  await page.getByRole("button", { name: "Stop and exit" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Yes, stop" }).click();
  await expect(page).toHaveURL(/\/kiosk$/);
  expect(cancelled).toEqual(["DELETE"]);
  expect(await page.evaluate(() => sessionStorage.getItem("osa.patient.session"))).toBeNull();
});
