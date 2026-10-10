import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-000000000105";

test("patient status page shows live queue position and a safe ETA range", async ({
  page,
}) => {
  const payload = {
    state: "waiting",
    token: "GEN-007",
    position: 4,
    eta_min: 5,
    eta_max: 10,
    department: { en: "General Medicine", hi: "सामान्य चिकित्सा", mr: "सामान्य औषधोपचार" },
    hospital: "Demo Government Hospital, Nagpur",
    language: "en",
    updated_at: "2026-10-06T05:00:00Z",
  };
  await page.route(/\/status\/status-test-token$/, (route) =>
    route.fulfill({ json: payload }),
  );
  await page.route(/\/status\/status-test-token\/stream$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `event: status\ndata: ${JSON.stringify(payload)}\n\n`,
    }),
  );
  await page.goto("/s/status-test-token");
  await expect(page.getByText("GEN-007")).toBeVisible();
  await expect(page.getByText("5–10 min")).toBeVisible();
  await expect(
    page.locator(".status-figure", { hasText: "People ahead" }).getByText("3", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("General Medicine")).toBeVisible();
  await expect(page.getByText("Demo Government Hospital, Nagpur")).toBeVisible();
  await expect(page.getByText(/urgent/i)).toHaveCount(0);
  const screenshotDirectory = resolve(
    process.cwd(),
    "..",
    "..",
    "docs",
    "screenshots",
    "phase-05",
  );
  mkdirSync(screenshotDirectory, { recursive: true });
  await page.screenshot({
    path: resolve(screenshotDirectory, "patient-status-en.png"),
    fullPage: true,
  });
});

test("a called patient sees where to go, in the language they used at the kiosk", async ({
  page,
}) => {
  const payload = {
    state: "called",
    token: "GEN-008",
    position: null,
    eta_min: 0,
    eta_max: 0,
    department: { en: "General Medicine", hi: "सामान्य चिकित्सा", mr: "सामान्य औषधोपचार" },
    hospital: null,
    language: "hi",
  };
  await page.route(/\/status\/called-test-token$/, (route) => route.fulfill({ json: payload }));
  await page.route(/\/status\/called-test-token\/stream$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `event: status\ndata: ${JSON.stringify(payload)}\n\n`,
    }),
  );
  await page.goto("/s/called-test-token");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("आपकी बारी आ गई!");
  await expect(alert).toContainText("सामान्य चिकित्सा");
  // The patient can switch language themselves.
  await page.getByRole("button", { name: "English" }).click();
  await expect(alert).toContainText("Please go to General Medicine now.");
});

test("staff can call a waiting patient from the queue board", async ({
  page,
}) => {
  let state = "queued";
  let queueRequests = 0;
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "staff-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-000000000501",
          name: "Demo Nurse",
          email: "nurse@example.test",
          role: "nurse",
          hospital_id: "00000000-0000-7000-8000-000000000500",
        },
      },
    }),
  );
  await page.route(/\/queue(?:\?.*)?$/, (route) => {
    queueRequests += 1;
    return route.fulfill({
      json: {
        items: [
          {
            visit_id: visitId,
            department_id: "00000000-0000-7000-8000-000000000510",
            token: "GEN-007",
            urgency: "routine",
            status: state,
            age_years: 37,
            sex: "unknown",
            complaint: "Synthetic queue complaint",
            position: 1,
            queued_at: "2026-10-06T05:00:00Z",
          },
        ],
        departments: [
          {
            id: "00000000-0000-7000-8000-000000000510",
            code: "GENMED",
            name: "General Medicine",
          },
        ],
        next_cursor: null,
        limit: 50,
      },
    });
  });
  await page.route(`**/queue/${visitId}/call`, async (route) => {
    state = "called";
    await route.fulfill({ json: { visit_id: visitId, status: state } });
  });
  await page.goto("/staff");
  await expect.poll(() => queueRequests).toBeGreaterThan(0);
  await expect(page.getByText("GEN-007")).toBeVisible();
  await page.getByRole("button", { name: "Call" }).click();
  await expect(
    page
      .getByRole("region", { name: "Queue patients" })
      .getByText("Called", { exact: true }),
  ).toBeVisible();
  const screenshotDirectory = resolve(
    process.cwd(),
    "..",
    "..",
    "docs",
    "screenshots",
    "phase-05",
  );
  mkdirSync(screenshotDirectory, { recursive: true });
  await page.screenshot({
    path: resolve(screenshotDirectory, "staff-queue-en.png"),
    fullPage: true,
  });
});
