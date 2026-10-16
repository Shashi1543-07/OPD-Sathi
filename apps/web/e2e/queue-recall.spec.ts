import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-00000000e001";
const department = "00000000-0000-7000-8000-00000000e010";

async function signedInNurse(page: Page) {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "nurse-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-00000000e002",
          name: "Demo Nurse",
          email: "nurse@example.test",
          role: "nurse",
          hospital_id: "00000000-0000-7000-8000-00000000e000",
        },
      },
    }),
  );
}

test("a patient who missed their call is told to come to the desk, in their language", async ({
  page,
}) => {
  const payload = {
    state: "skipped",
    token: "GEN-011",
    position: null,
    eta_min: 0,
    eta_max: 0,
    department: { en: "General Medicine", hi: "सामान्य चिकित्सा", mr: "सामान्य औषधोपचार" },
    hospital: null,
    language: "hi",
  };
  await page.route(/\/status\/missed-test-token$/, (route) => route.fulfill({ json: payload }));
  await page.route(/\/status\/missed-test-token\/stream$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `event: status\ndata: ${JSON.stringify(payload)}\n\n`,
    }),
  );
  await page.goto("/s/missed-test-token");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("आपकी बारी छूट गई");
  await page.getByRole("button", { name: "English" }).click();
  await expect(alert).toContainText("We missed you");
  await expect(alert).toContainText("Staff will put you back in the queue with the same token.");
  // A missed call is not a place in the line.
  await expect(page.getByText("People ahead")).toHaveCount(0);
});

test("a nurse marks a called patient absent and recalls them to their place", async ({ page }) => {
  let state: "called" | "skipped" | "queued" = "called";
  const actions: string[] = [];
  await signedInNurse(page);
  await page.route(/\/queue(?:\?.*)?$/, (route) => {
    const patient = {
      visit_id: visitId,
      department_id: department,
      token: "MED-007",
      urgency: "routine",
      age_years: 58,
      sex: "male",
      complaint: "Dizziness since morning",
      queued_at: "2026-10-16T04:00:00Z",
    };
    const waiting = {
      ...patient,
      visit_id: "00000000-0000-7000-8000-00000000e003",
      token: "MED-008",
      complaint: "Cough",
      status: "queued",
      position: 2,
    };
    return route.fulfill({
      json: {
        items:
          state === "skipped"
            ? [{ ...waiting, position: 1 }]
            : [
                {
                  ...patient,
                  status: state,
                  position: 1,
                  // Called ten minutes ago: well past the three-minute grace period.
                  called_at: new Date(Date.now() - 600_000).toISOString(),
                  called_by_name: state === "called" ? "Dr Rao" : null,
                  skip_count: state === "queued" ? 1 : 0,
                },
                waiting,
              ],
        missed:
          state === "skipped"
            ? [{ ...patient, skip_count: 1, skipped_at: new Date().toISOString() }]
            : [],
        departments: [{ id: department, code: "GENMED", name: "General Medicine" }],
        next_cursor: null,
        limit: 50,
        call_grace_seconds: 180,
      },
    });
  });
  await page.route(`**/queue/${visitId}/skip`, (route) => {
    actions.push("skip");
    state = "skipped";
    return route.fulfill({ json: { visit_id: visitId, status: "skipped", token: "MED-007" } });
  });
  await page.route(`**/queue/${visitId}/recall`, (route) => {
    actions.push("recall");
    state = "queued";
    return route.fulfill({ json: { visit_id: visitId, status: "queued", token: "MED-007" } });
  });

  await page.goto("/staff");
  const line = page.getByRole("region", { name: "Queue patients" });
  await expect(line.getByText("Called by Dr Rao")).toBeVisible();
  await line.getByRole("button", { name: "Mark absent" }).click();

  const missed = page.getByRole("region", { name: "Missed calls" });
  await expect(missed.getByText("MED-007")).toBeVisible();
  await expect(line.getByText("MED-007")).toHaveCount(0);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);
  // At phone width the missed-calls panel still fits.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(missed.getByRole("button", { name: "Recall" })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(0);

  await missed.getByRole("button", { name: "Recall" }).click();
  await expect(page.getByRole("region", { name: "Missed calls" })).toHaveCount(0);
  await expect(line.getByText("MED-007")).toBeVisible();
  await expect(line.getByText("Missed 1 call")).toBeVisible();
  expect(actions).toEqual(["skip", "recall"]);
});
