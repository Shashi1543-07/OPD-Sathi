import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// The queue board in Hindi and Marathi (longer words than English) at every width, and the
// nurse's two safety actions with the keyboard alone (Phase 7, 7.12.2 and 7.12.5).
// Synthetic data only.
const department = { id: "00000000-0000-7000-8000-000000000e10", code: "GENMED", name: "General Medicine" };
const item = (n: number, extra: Record<string, unknown>) => ({
  visit_id: `00000000-0000-7000-8000-00000000e00${n}`,
  department_id: department.id,
  token: `GEN-00${n}`,
  urgency: "routine",
  status: "queued",
  age_years: 67,
  sex: "female",
  complaint: "Breathlessness on climbing stairs for two weeks, with swelling of both feet",
  position: n,
  queued_at: "2026-10-10T08:00:00Z",
  ...extra,
});

async function signedInNurse(page: Page) {
  const sent: string[] = [];
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "staff-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: "00000000-0000-7000-8000-000000000e02",
          name: "Synthetic Nurse",
          email: "nurse@example.test",
          role: "nurse",
          hospital_id: "00000000-0000-7000-8000-000000000e00",
        },
      },
    }),
  );
  await page.route(/\/queue(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        items: [
          item(1, { urgency: "urgent", nurse_check: true }),
          item(2, { complaint: "Fever", needs_assistance: true }),
          item(3, { complaint: "Rash", status: "called", called_at: "2026-10-10T08:30:00Z" }),
        ],
        departments: [department],
        next_cursor: null,
        limit: 50,
      },
    }),
  );
  let alerts = [
    {
      id: "00000000-0000-7000-8000-00000000ea01",
      visit_id: "00000000-0000-7000-8000-00000000e001",
      rule_id: "CHEST_PAIN",
      level: "urgent",
      evidence: "chest_pain",
      reason_en: "Chest pain reported at the kiosk",
      token_label: "GEN-001",
    },
  ];
  await page.route(/\/alerts(?:\?.*)?$/, (route) => route.fulfill({ json: { items: alerts } }));
  await page.route(/\/alerts\/[^/]+\/ack$/, async (route) => {
    sent.push("ack");
    alerts = [];
    await route.fulfill({ json: { acknowledged: true } });
  });
  await page.route(/\/queue\/call-next\?/, async (route) => {
    sent.push("call-next");
    await route.fulfill({ json: { visit_id: item(1, {}).visit_id, status: "called", token: "GEN-001" } });
  });
  return sent;
}

for (const language of ["hi", "mr"] as const) {
  test(`the queue board in ${language} fits every width and passes an accessibility scan`, async ({
    page,
  }) => {
    await signedInNurse(page);
    await page.goto(`/staff?lang=${language}`);
    const list = page.locator(".qb-item");
    await expect(list).toHaveCount(3);
    await expect(page.getByRole("button", { name: language === "hi" ? "पुष्टि करें" : "खात्री करा" })).toBeVisible();
    for (const width of [1600, 1280, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(
          () =>
            page.evaluate(() => {
              const main = document.querySelector(".staff-main");
              return {
                page: document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth,
                main: main ? main.scrollWidth - main.clientWidth : 0,
                offscreen: [...document.querySelectorAll(".qb-item .qb-actions > *, .qb-alert button")].filter(
                  (element) => element.getBoundingClientRect().right > document.documentElement.clientWidth + 1,
                ).length,
              };
            }),
          { message: `${language} at ${width}px` },
        )
        .toEqual({ page: 0, main: 0, offscreen: 0 });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}

test("a nurse acknowledges an urgent alert and calls the next patient with the keyboard alone", async ({
  page,
}) => {
  const sent = await signedInNurse(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/staff?lang=en");
  await expect(page.locator(".qb-item")).toHaveCount(3);

  // Tab until the named control has focus, as someone without a mouse would.
  async function tabTo(name: string) {
    const target = page.getByRole("button", { name, exact: true });
    for (let presses = 0; presses < 80; presses += 1) {
      await page.keyboard.press("Tab");
      if (await target.evaluate((element) => element === document.activeElement)) return;
    }
    throw new Error(`${name} cannot be reached with Tab`);
  }

  await tabTo("Acknowledge");
  // Where the focus is must be visible.
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement!).outlineStyle);
  expect(outline).not.toBe("none");
  await page.keyboard.press("Enter");
  await expect.poll(() => sent).toEqual(["ack"]);
  await expect(page.getByRole("button", { name: "Acknowledge", exact: true })).toHaveCount(0);

  await tabTo("Call next");
  await page.keyboard.press("Enter");
  await expect.poll(() => sent).toEqual(["ack", "call-next"]);
  await expect(page.getByText("Called GEN-001.")).toBeVisible();
});
