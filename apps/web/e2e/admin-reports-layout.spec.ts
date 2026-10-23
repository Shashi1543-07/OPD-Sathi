import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const hospitalId = "00000000-0000-7000-8000-00000000a000";
const departments = Array.from({ length: 14 }, (_, index) => ({
  id: `00000000-0000-7000-8000-0000000d${String(index).padStart(4, "0")}`,
  code: `DEPT_${index}`,
  token_prefix: `D${index}`,
  name_en: index === 3 ? "Obstetrics, Gynaecology and Reproductive Medicine" : `Department ${index}`,
  name_hi: `विभाग ${index}`,
  name_mr: `विभाग ${index}`,
  is_active: index !== 5,
  created_at: "2026-10-01T10:00:00Z",
}));
const users = Array.from({ length: 40 }, (_, index) => ({
  id: index === 0 ? "00000000-0000-7000-8000-00000000a001" : `00000000-0000-7000-8000-0000000u${String(index).padStart(4, "0")}`,
  role: index === 0 ? "admin" : index % 3 === 0 ? "nurse" : "doctor",
  name: index === 0 ? "Demo Administrator" : `Demo Staff ${String(index).padStart(2, "0")}`,
  email: `staff${index}@demo.opdsaathi.test`,
  department_id: departments[index % departments.length].id,
  is_active: index % 7 !== 6,
  created_at: "2026-10-01T10:00:00Z",
}));
const kiosks = Array.from({ length: 12 }, (_, index) => ({
  id: `00000000-0000-7000-8000-0000000k${String(index).padStart(4, "0")}`,
  label: index < 4 ? "Reception kiosk" : `Ward kiosk ${index}`,
  is_active: index !== 2,
  // One kiosk reported a moment ago, one two hours ago, the rest never.
  last_seen_at:
    index === 0
      ? new Date(Date.now() - 30_000).toISOString()
      : index === 1
        ? new Date(Date.now() - 2 * 3_600_000).toISOString()
        : null,
  created_at: "2026-10-02T09:30:00Z",
}));

async function signedInAdmin(page: Page) {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: "admin-test-token",
        token_type: "bearer",
        expires_in: 600,
        user: {
          id: users[0].id,
          name: "Demo Administrator",
          email: "admin@demo.opdsaathi.test",
          role: "admin",
          hospital_id: hospitalId,
        },
      },
    }),
  );
  await page.route(/\/admin\/(users|kiosks|departments)\?/, (route) => {
    const resource = new URL(route.request().url()).pathname.split("/").pop();
    const items = resource === "users" ? users : resource === "kiosks" ? kiosks : departments;
    return route.fulfill({ json: { items, next_cursor: null, limit: 100 } });
  });
  const days = Array.from({ length: 30 }, (_, index) => {
    const date = new Date(Date.UTC(2026, 8, 8 + index)).toISOString().slice(0, 10);
    return index % 6 === 5
      ? []
      : [
          { service_date: date, patient_count: 10 + ((index * 7) % 23) },
          { service_date: date, patient_count: 2 },
        ];
  }).flat();
  const total = days.reduce((sum, item) => sum + item.patient_count, 0);
  await page.route("**/analytics/settings", (route) => route.fulfill({ json: {
    minutes_saved_per_visit: 3, is_default: true, updated_at: null, updated_by_name: null,
  } }));
  await page.route(/\/analytics\/summary\?/, (route) =>
    route.fulfill({
      json: {
        patients: total,
        average_wait_seconds: null,
        average_consult_seconds: null,
        urgent_count: 14,
        language_mix: { en: total - 1, mr: 1 },
        average_interview_seconds: 24,
        confirmed_without_edits_percent: 100,
        doctor_edit_rate_percent: 0,
        department_load: departments.slice(0, 3).map((department, index) => ({
          department_id: department.id,
          name: { en: department.name_en, hi: department.name_hi, mr: department.name_mr },
          patients: [total - 40, 30, 10][index],
          urgent_count: index,
          average_wait_seconds: null,
        })),
        start_day: "2026-09-08",
        end_day: "2026-10-07",
      },
    }),
  );
  await page.route(/\/analytics\/timeseries\?/, (route) =>
    route.fulfill({ json: { days: 30, items: days } }),
  );
}

async function overflow(page: Page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const main = document.querySelector(".staff-main");
    const sticking = [...document.querySelectorAll(".adm *, .rp *")]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        if (box.width === 0 || box.right <= vw + 1) return false;
        // Content inside a deliberate horizontal scroller (the department table) is fine.
        return !element.closest(".rp-table-wrap");
      })
      .map((element) => `${element.tagName.toLowerCase()}.${[...element.classList].join(".")}`)
      .slice(0, 3);
    return {
      page: document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth,
      main: main ? main.scrollWidth - main.clientWidth : 0,
      sticking,
    };
  });
}

const WIDTHS = [1600, 1440, 1366, 1024, 768, 390, 320];

test("settings keeps the heading and tabs in place while long lists scroll inside their panel", async ({
  page,
}) => {
  await signedInAdmin(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/staff/settings");
  const list = page.getByRole("region", { name: "Staff accounts list" });
  await expect(list.getByText("Demo Staff 39")).toBeAttached();
  await expect(page.getByText("40 records")).toBeVisible();

  const heading = page.getByRole("heading", { name: "Administration" });
  const before = await heading.boundingBox();
  const box = await list.boundingBox();
  // The list fills the space left under the heading instead of making the page 40 rows tall.
  expect(box!.y + box!.height).toBeLessThanOrEqual(900);
  await list.evaluate((element) => element.scrollTo(0, element.scrollHeight));
  const lastAction = page.getByRole("button", { name: "Activate Demo Staff 34" });
  await expect(lastAction).toBeInViewport();
  expect(await heading.boundingBox()).toEqual(before);
  await expect(page.getByRole("tab", { name: "Users" })).toBeInViewport();
  // The header row stays visible above the scrolled rows.
  await expect(list.locator("thead th").first()).toBeInViewport();

  // Search narrows the same list; deactivating yourself is never offered.
  await page.getByRole("searchbox", { name: "Search by name or email" }).fill("staff3");
  await expect(page.getByText(/Showing 11 of 40/)).toBeVisible();
  await page.getByRole("searchbox", { name: "Search by name or email" }).fill("");
  await expect(page.getByRole("button", { name: "Deactivate Demo Administrator" })).toBeDisabled();

  for (const tab of ["Users", "Kiosks", "Departments"]) {
    await page.getByRole("tab", { name: tab }).click();
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: width < 800 ? 844 : 900 });
      await expect
        .poll(() => overflow(page), { message: `${tab} at ${width}px` })
        .toEqual({ page: 0, main: 0, sticking: [] });
      // Every row action is fully on screen horizontally.
      const offscreen = await page.evaluate(() =>
        [...document.querySelectorAll(".adm-toggle")].filter((button) => {
          const box = button.getBoundingClientRect();
          const panel = button.closest(".adm-list")!.getBoundingClientRect();
          return box.left < panel.left || box.right > panel.right;
        }).length,
      );
      expect(offscreen, `${tab} actions at ${width}px`).toBe(0);
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  }

  // Kiosks with the same name are told apart by their ID; "online" comes only from the
  // kiosk's own recent heartbeat, never from the account being active.
  await page.getByRole("tab", { name: "Kiosks" }).click();
  const kioskList = page.getByRole("region", { name: "Registration kiosks list" });
  await expect(kioskList.getByText("Reception kiosk")).toHaveCount(4);
  await expect(kioskList.getByText("ID …000k0000")).toBeVisible();
  await expect(page.getByText("1 of 11 active kiosks online now")).toBeVisible();
  await expect(kioskList.getByText("Online", { exact: true })).toHaveCount(1);
  await expect(kioskList.getByText("Offline", { exact: true })).toHaveCount(1);
  await expect(kioskList.getByText("Last seen 2 hours ago")).toBeVisible();
  await expect(kioskList.getByText("Turned off", { exact: true })).toHaveCount(1);
  await expect(kioskList.getByText("Not seen yet", { exact: true })).toHaveCount(9);

  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const serious = result.violations.filter((violation) =>
    ["serious", "critical"].includes(violation.impact ?? ""),
  );
  expect(serious.map((violation) => `${violation.id}: ${violation.nodes[0]?.target}`)).toEqual([]);
});

test("reports keep even card rows, a reconciled chart, and no sideways scrolling", async ({ page }) => {
  await signedInAdmin(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/staff/reports");
  await expect(page.getByRole("img", { name: /^Daily patient visits, Last 30 days/ })).toBeVisible();
  // Missing averages read "No data", never "— min".
  const wait = page.locator(".rp-kpi", { hasText: "Average wait" });
  await expect(wait).toContainText("No data");
  await expect(wait).not.toContainText("—");

  const columns = async () =>
    page.evaluate(() =>
      [...document.querySelectorAll(".rp-kpis")].map((grid) => {
        const cards = [...grid.children].map((card) => card.getBoundingClientRect());
        return {
          perRow: new Set(cards.map((card) => Math.round(card.top))).size === 1 ? cards.length : cards.filter((card) => Math.round(card.top) === Math.round(cards[0].top)).length,
          heights: new Set(
            cards
              .filter((card) => Math.round(card.top) === Math.round(cards[0].top))
              .map((card) => Math.round(card.height)),
          ).size,
        };
      }),
    );
  expect(await columns()).toEqual([
    { perRow: 4, heights: 1 },
    { perRow: 4, heights: 1 },
  ]);

  // The chart's total is the same number as the visits card.
  const visits = await page.locator(".rp-kpi", { hasText: "Patient visits" }).locator(".rp-kpi-value").innerText();
  await expect(page.locator(".rp-trend .rp-card-meta")).toContainText(`${visits} visits`);
  await page.locator(".rp-chart-frame").focus();
  await page.keyboard.press("End");
  // The last day of the fixture has no visits: it is a real zero, not a gap.
  await expect(page.locator(".rp-tooltip")).toContainText("0 visits");
  await expect(page.locator(".rp-tooltip")).toContainText(/Oct 7, 2026|7 Oct 2026/);

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 });
    await expect
      .poll(() => overflow(page), { message: `reports at ${width}px` })
      .toEqual({ page: 0, main: 0, sticking: [] });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await columns())[0].perRow).toBe(1);

  await page.setViewportSize({ width: 1440, height: 900 });
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const serious = result.violations.filter((violation) =>
    ["serious", "critical"].includes(violation.impact ?? ""),
  );
  expect(serious.map((violation) => `${violation.id}: ${violation.nodes[0]?.target}`)).toEqual([]);
});
