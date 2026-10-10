import { expect, test, type Page } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-000000000c01";

async function signedInNurse(page: Page, triage: object) {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "staff-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-000000000c02",
          name: "Demo Nurse",
          email: "nurse@example.test",
          role: "nurse",
          hospital_id: "00000000-0000-7000-8000-000000000c00",
        },
      },
    }),
  );
  await page.route(/\/queue(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        items: [
          {
            visit_id: visitId,
            department_id: "00000000-0000-7000-8000-000000000c10",
            token: "GEN-021",
            urgency: "priority",
            status: "queued",
            age_years: 70,
            sex: "male",
            complaint: "Difficulty breathing",
            position: 1,
            queued_at: "2026-10-10T12:00:00Z",
            triage,
          },
        ],
        departments: [
          { id: "00000000-0000-7000-8000-000000000c10", code: "GENMED", name: "General Medicine" },
        ],
        next_cursor: null,
        limit: 50,
      },
    }),
  );
}

test("staff see the triage score and every point that made it up", async ({ page }) => {
  await signedInNurse(page, {
    score: 36,
    level: "priority",
    head_start_min: 22,
    components: [
      { points: 12, label: "Difficulty breathing is the main problem", kind: "signal" },
      { points: 10, label: "Breathless on walking", kind: "signal" },
      { points: 8, label: "Aged 65 to 74", kind: "vulnerability" },
      { points: 6, label: "Started today", kind: "onset" },
    ],
    ai_review: { verdict: "agree", reason_en: "Fits a 70-year-old who is breathless on walking." },
  });
  await page.goto("/staff");
  const row = page.getByRole("region", { name: "Queue patients" });
  await row.getByText("Score 36").click();
  await expect(row.getByText("36/100 · Priority")).toBeVisible();
  await expect(row.getByText("Patients never see this score.")).toBeVisible();
  await expect(row.getByText("Breathless on walking", { exact: true })).toBeVisible();
  await expect(row.getByText(/22-minute head start, never more than 45/)).toBeVisible();
  await expect(row.getByText(/AI check agrees with this level/)).toBeVisible();
});

async function activatedKiosk(page: Page) {
  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.addInitScript(() => localStorage.setItem("osa.kiosk.access", "synthetic-kiosk-token"));
}

test("the emergency button calls staff at once, with no form", async ({ page }) => {
  await activatedKiosk(page);
  let calls = 0;
  await page.route("**/intake/emergency", async (route) => {
    calls += 1;
    expect(route.request().headers().authorization).toBe("Bearer synthetic-kiosk-token");
    await route.fulfill({ status: 201, json: { alerted: true, repeat: false } });
  });
  await page.goto("/kiosk");
  await page.getByRole("button", { name: "Emergency? Get help now" }).click();
  await expect(page.getByRole("heading", { name: "Help is coming" })).toBeVisible();
  await expect(page.getByText(/go to Casualty \(Emergency\) now/)).toBeVisible();
  expect(calls).toBe(1);
  await page.getByRole("button", { name: "Back to start" }).click();
  await expect(page.getByRole("button", { name: "Start patient check-in" })).toBeVisible();
});

test("if the emergency alert cannot be sent, the kiosk still says what to do", async ({ page }) => {
  await activatedKiosk(page);
  await page.route("**/intake/emergency", (route) => route.fulfill({ status: 503, json: {} }));
  await page.goto("/kiosk");
  await page.getByRole("button", { name: "Emergency? Get help now" }).click();
  await expect(page.getByRole("heading", { name: /could not be sent/ })).toBeVisible();
  await expect(page.getByText(/shout for help and go to Casualty/)).toBeVisible();
});
