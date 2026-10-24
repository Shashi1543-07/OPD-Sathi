import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("a nurse re-issues a lost OPD card after checking the patient's details", async ({ page }) => {
  const reissued: unknown[] = [];
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "synthetic-nurse-token",
        token_type: "bearer",
        expires_in: 300,
        user: {
          id: "synthetic-nurse-id",
          name: "Nurse Patil",
          email: "nurse@example.test",
          role: "nurse",
          hospital_id: "synthetic-hospital-id",
        },
      },
    }),
  );
  await page.route((url) => /^(?:\/api)?\/patients$/.test(url.pathname), (route) =>
    route.fulfill({ json: { limit: 200, items: [] } }),
  );
  await page.route("**/patients/lookup?*", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            opd_number: "26-000042",
            age_years: 41,
            sex: "female",
            visit_count: 3,
            last_visit: "2026-10-01",
            card_version: 1,
          },
        ],
      },
    }),
  );
  await page.route("**/patients/card/reissue", async (route) => {
    reissued.push(route.request().postDataJSON());
    await route.fulfill({
      json: { opd_number: "26-000042", pin: "482913", card_version: 2 },
      headers: { "Cache-Control": "no-store" },
    });
  });

  await page.goto("/staff/patients");
  const tool = page.getByRole("article", { name: "Lost OPD card" });
  await tool.getByRole("combobox", { name: "Search by" }).selectOption("phone");
  await tool.getByRole("textbox", { name: "Phone number" }).fill("9876500009");
  await tool.getByRole("button", { name: "Search" }).click();
  await expect(tool.getByText("26-000042")).toBeVisible();
  await tool.getByRole("button", { name: "Re-issue card" }).click();
  await expect(tool.getByText(/Age and sex checked with the patient/)).toBeVisible();
  await tool.getByRole("button", { name: "Yes, re-issue" }).click();

  await expect(tool.getByRole("status")).toContainText("New card for 26-000042. PIN:");
  await expect(tool.getByText("482913")).toBeVisible();
  expect(reissued).toEqual([{ opd_number: "26-000042", card_version: 1 }]);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(tool.getByText("482913")).toBeVisible();
  expect(
    await page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth),
  ).toBeLessThanOrEqual(0);
});
