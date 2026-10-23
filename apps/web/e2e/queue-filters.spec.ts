import { expect, test, type Page } from "@playwright/test";

const department = { id: "00000000-0000-7000-8000-000000000d10", code: "GENMED", name: "General Medicine" };
const item = (n: number, extra: Record<string, unknown>) => ({
  visit_id: `00000000-0000-7000-8000-00000000d00${n}`,
  department_id: department.id,
  token: `GEN-00${n}`,
  urgency: "routine",
  status: "queued",
  age_years: 40,
  sex: "female",
  complaint: "Cough",
  position: n,
  queued_at: "2026-10-10T08:00:00Z",
  ...extra,
});

async function signedInNurse(page: Page) {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "staff-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-000000000d02",
          name: "Demo Nurse",
          email: "nurse@example.test",
          role: "nurse",
          hospital_id: "00000000-0000-7000-8000-000000000d00",
        },
      },
    }),
  );
  await page.route(/\/queue(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        items: [
          item(1, { complaint: "Cough" }),
          item(2, { complaint: "Chest pain and sweating", urgency: "urgent" }),
          item(3, { complaint: "Rash", status: "called" }),
        ],
        departments: [department],
        next_cursor: null,
        limit: 50,
      },
    }),
  );
  await page.route(/\/alerts(?:\?.*)?$/, (route) => route.fulfill({ json: { items: [] } }));
}

test("search, priority filter and KPI cards narrow the queue; next action asks the server to call", async ({
  page,
}) => {
  await signedInNurse(page);
  const calls: string[] = [];
  // The server chooses whom to call, so two doctors pressing at once get different patients.
  await page.route(/\/queue\/call-next\?/, async (route) => {
    calls.push(route.request().url());
    await route.fulfill({
      json: { visit_id: "00000000-0000-7000-8000-00000000d002", status: "called", token: "GEN-002" },
    });
  });
  await page.goto("/staff");
  const list = page.getByRole("region", { name: "Queue patients" });
  await expect(list.locator(".qb-item")).toHaveCount(3);
  await expect(page.getByRole("combobox", { name: "Department" })).toHaveValue(department.id);

  // Urgent first, then arrival order: the next patient to call is the urgent one.
  const next = page.getByRole("region", { name: "Next action" });
  await expect(next.getByText("GEN-002", { exact: true })).toBeVisible();

  await page.getByRole("searchbox", { name: "Search token or complaint" }).fill("chest");
  await expect(list.locator(".qb-item")).toHaveCount(1);
  await expect(page.getByText("1 of 3")).toBeVisible();
  await page.getByRole("searchbox", { name: "Search token or complaint" }).fill("");

  await page.getByRole("checkbox", { name: "Priority / urgent only" }).check();
  await expect(list.locator(".qb-item")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /Priority \/ urgent/ })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("checkbox", { name: "Priority / urgent only" }).uncheck();

  // Counts always describe the whole queue, whatever is filtered.
  await expect(page.getByRole("button", { name: /Waiting/ }).first()).toContainText("2");

  await next.getByRole("button", { name: "Call next" }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0]).toContain(`department_id=${department.id}`);
  await expect(page.getByText("Called GEN-002.")).toBeVisible();
});

test("the queue never scrolls sideways, from a wide desktop down to a narrow phone", async ({
  page,
}) => {
  await signedInNurse(page);
  await page.goto("/staff");
  await expect(page.getByRole("region", { name: "Queue patients" }).locator(".qb-item")).toHaveCount(3);
  for (const width of [1600, 1366, 1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    // Re-measured until the layout has settled at the new width: no fixed wait.
    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const main = document.querySelector(".staff-main");
            return {
              page: document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth,
              main: main ? main.scrollWidth - main.clientWidth : 0,
              actionsOffscreen: [...document.querySelectorAll(".qb-item .qb-actions > *")].filter(
                (el) => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1,
              ).length,
            };
          }),
        { message: `at ${width}px` },
      )
      .toEqual({ page: 0, main: 0, actionsOffscreen: 0 });
  }
});
