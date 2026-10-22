import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Phase 7 (7.3.1, 7.3.2): two-step sign-in and a forced password change, synthetic data only.
const HOSPITAL = "00000000-0000-7000-8000-000000000e00";
const user = {
  id: "00000000-0000-7000-8000-000000000e01",
  name: "Synthetic Administrator",
  email: "admin@example.test",
  role: "admin",
  hospital_id: HOSPITAL,
};
const session = (restrictions: string[]) => ({
  access_token: "staff-test-token",
  token_type: "bearer",
  expires_in: 600,
  restrictions,
  user: { ...user, mfa_enabled: true },
});

async function noAccessibilityViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
}

test("sign-in asks for the authenticator code, then a new password, then opens the app", async ({
  page,
}) => {
  const sent: Record<string, unknown>[] = [];
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, json: {} }));
  await page.route("**/auth/hospitals", (route) =>
    route.fulfill({ json: { items: [{ id: HOSPITAL, name: "Synthetic District Hospital" }] } }),
  );
  await page.route("**/auth/login", (route) =>
    route.fulfill({ json: { mfa_required: true, mfa_token: "synthetic-challenge", expires_in: 300 } }),
  );
  let codeTries = 0;
  await page.route("**/auth/mfa/verify", async (route) => {
    sent.push(route.request().postDataJSON() as Record<string, unknown>);
    codeTries += 1;
    if (codeTries === 1) {
      await route.fulfill({ status: 401, json: { code: "INVALID_MFA_CODE" } });
      return;
    }
    await route.fulfill({ json: session(["password_change"]) });
  });
  await page.route("**/auth/password", async (route) => {
    sent.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ json: session([]) });
  });

  await page.goto("/staff");
  await page.getByLabel("Email").fill("admin@example.test");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password-2031");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText("Enter the 6-digit code from your authenticator app.")).toBeVisible();
  await noAccessibilityViolations(page);
  await page.getByLabel("6-digit code").fill("111111");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page.getByText(/That code was not accepted/)).toBeVisible();
  await page.getByLabel("6-digit code").fill("123 456");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  expect(sent[1]).toEqual({ mfa_token: "synthetic-challenge", code: "123456" });

  // A reset password must be replaced before anything else opens.
  await expect(page.getByRole("heading", { name: "Choose your own password" })).toBeVisible();
  await noAccessibilityViolations(page);
  await page.getByLabel("Current password").fill("abcd-efgh-jkmn-pqrs");
  await page.getByLabel("New password", { exact: true }).fill("lantern orchard river 2031");
  await page.getByLabel("Type the new password again").fill("lantern orchard river 2031");
  await page.getByRole("button", { name: "Save new password" }).click();
  await expect(page.getByRole("heading", { name: "Choose your own password" })).toHaveCount(0);
  expect(sent[2]).toEqual({
    current_password: "abcd-efgh-jkmn-pqrs",
    new_password: "lantern orchard river 2031",
  });
  await expect(page.getByRole("link", { name: "My account" })).toBeVisible();
});

test("a recovery code can stand in for the phone", async ({ page }) => {
  let body: Record<string, unknown> | undefined;
  await page.route("**/auth/refresh", (route) => route.fulfill({ status: 401, json: {} }));
  await page.route("**/auth/hospitals", (route) =>
    route.fulfill({ json: { items: [{ id: HOSPITAL, name: "Synthetic District Hospital" }] } }),
  );
  await page.route("**/auth/login", (route) =>
    route.fulfill({ json: { mfa_required: true, mfa_token: "synthetic-challenge", expires_in: 300 } }),
  );
  await page.route("**/auth/mfa/verify", async (route) => {
    body = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: session([]) });
  });
  await page.goto("/staff");
  await page.getByLabel("Email").fill("admin@example.test");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password-2031");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("button", { name: "Lost your phone? Use a recovery code" }).click();
  await page.getByLabel("Recovery code").fill("abcd-efgh-jkmn");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page.getByRole("link", { name: "My account" })).toBeVisible();
  expect(body).toEqual({ mfa_token: "synthetic-challenge", recovery_code: "abcd-efgh-jkmn" });
});
