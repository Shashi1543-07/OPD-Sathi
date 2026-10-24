import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

const session = {
  visit_id: "00000000-0000-7000-8000-000000000901",
  patient_access_token: "synthetic-patient-token",
  consent: {
    language: "en",
    version: "synthetic-consent-v1",
    sha256: "a".repeat(64),
    text: "Synthetic consent text for layout review.",
  },
  stage: "identity",
};

const screenshots = path.resolve(
  process.cwd(),
  "../../docs/screenshots/phase-14",
);
mkdirSync(screenshots, { recursive: true });

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "phone", width: 390, height: 844 },
]) {
  test(`kiosk identity form fits ${viewport.name} viewport`, async ({
    page,
  }) => {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await page.addInitScript((storedSession) => {
      sessionStorage.setItem(
        "osa.patient.session",
        JSON.stringify(storedSession),
      );
    }, session);
    await page.route("**/intake/capabilities", (route) =>
      route.fulfill({ json: { stt: "browser", tts: "browser", ai: false } }),
    );
    await page.route("**/healthz", (route) =>
      route.fulfill({ json: { status: "ok" } }),
    );
    await page.goto("/kiosk/intake");

    await expect(
      page.getByRole("heading", { name: "Tell us a little about yourself" }),
    ).toBeVisible();
    const continueButton = page.getByRole("button", { name: "Next", exact: true });
    await expect(continueButton).toBeInViewport();
    await expect(page.locator(".intake-card")).toBeInViewport();

    const dimensions = await page.evaluate(() => ({
      viewport: { width: window.innerWidth, height: window.innerHeight },
      document: {
        width: document.documentElement.scrollWidth,
        height: document.documentElement.scrollHeight,
      },
      body: {
        width: document.body.scrollWidth,
        height: document.body.scrollHeight,
      },
      overflow: [...document.querySelectorAll("body *")]
        .map((element) => ({
          tag: element.tagName,
          className:
            typeof element.className === "string" ? element.className : "",
          right: Math.round(element.getBoundingClientRect().right),
        }))
        .filter((element) => element.right > window.innerWidth + 1),
    }));
    expect(
      dimensions.document.width,
      JSON.stringify(dimensions),
    ).toBeLessThanOrEqual(viewport.width);
    expect(
      dimensions.document.height,
      JSON.stringify(dimensions),
    ).toBeLessThanOrEqual(viewport.height);

    await page.screenshot({
      path: path.join(screenshots, `kiosk-identity-${viewport.name}.png`),
      animations: "disabled",
    });
  });
}

test("a child's details ask who is answering before going on", async ({ page }) => {
  const sent: unknown[] = [];
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript((storedSession) => {
    sessionStorage.setItem("osa.patient.session", JSON.stringify(storedSession));
  }, session);
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "browser", tts: "browser", ai: false } }),
  );
  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/intake/sessions/*/identity", async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({ json: { next: "danger_signs" } });
  });
  await page.goto("/kiosk/intake");

  await page.getByLabel("How old are you?").fill("6");
  await page.getByRole("radio", { name: "Female" }).click();
  const question = page.getByRole("radiogroup", { name: "Who is answering for this child?" });
  await expect(question).toBeVisible();
  const next = page.getByRole("button", { name: "Next", exact: true });
  await expect(next).toBeDisabled();
  await question.getByRole("radio", { name: "I am a parent or guardian" }).click();
  await expect(next).toBeEnabled();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(0);
  await next.click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ age: 6, sex: "female", answered_by: "guardian" });
});
