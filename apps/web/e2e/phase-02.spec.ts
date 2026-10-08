import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

const screenshotPath = path.resolve("..", "..", "docs", "screenshots", "phase-02");
mkdirSync(screenshotPath, { recursive: true });

const hospitalId = "11111111-1111-4111-8111-111111111111";
const user = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "Demo Admin",
  email: "admin@example.test",
  role: "admin",
  hospital_id: hospitalId,
};
const authResponse = {
  access_token: "synthetic-test-token",
  token_type: "bearer",
  expires_in: 600,
  user,
};

async function prepare(page: import("@playwright/test").Page) {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/problem+json",
      body: JSON.stringify({ code: "UNAUTHENTICATED", detail: "Sign in." }),
    }),
  );
  await page.route("**/auth/hospitals", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }),
  );
  await page.goto("/staff?lang=en");
  await expect(page.getByLabel("Hospital", { exact: true })).toBeVisible();
}

async function fillCredentials(page: import("@playwright/test").Page) {
  await page.getByLabel("Hospital", { exact: true }).fill(hospitalId);
  await page.getByLabel("Email").fill("admin@example.test");
  await page.getByLabel("Password").fill("synthetic-test-password");
}

test("staff can sign in and sign out", async ({ page }) => {
  await prepare(page);
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(accessibility.violations.map(({ id }) => id)).toEqual([]);
  await page.screenshot({ path: path.join(screenshotPath, "staff-login.png"), fullPage: true });
  await page.route("**/auth/login", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(authResponse) }),
  );
  await fillCredentials(page);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Demo Admin" })).toBeVisible();
  await expect(page.getByRole("navigation").getByText("Reports")).toBeVisible();
  await page.route("**/auth/logout", (route) => route.fulfill({ status: 204 }));
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByLabel("Hospital", { exact: true })).toBeVisible();
});

test("staff sign-in form supports keyboard-only use", async ({ page }) => {
  await prepare(page);
  await page.getByLabel("Hospital", { exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Email")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeFocused();
});

test("wrong password uses a generic sign-in message", async ({ page }) => {
  await prepare(page);
  await page.route("**/auth/login", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/problem+json",
      body: JSON.stringify({ code: "INVALID_CREDENTIALS", detail: "Could not verify." }),
    }),
  );
  await fillCredentials(page);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("The hospital ID, email, or password could not be verified.")).toBeVisible();
});

test("locked accounts see the temporary lockout message", async ({ page }) => {
  await prepare(page);
  await page.route("**/auth/login", (route) =>
    route.fulfill({
      status: 423,
      contentType: "application/problem+json",
      body: JSON.stringify({ code: "ACCOUNT_LOCKED", detail: "Wait." }),
    }),
  );
  await fillCredentials(page);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Sign-in is paused after repeated attempts. Try again in 15 minutes.")).toBeVisible();
});
