// Drives the kiosk intake and staff screens end to end and saves a screenshot per step.
// Usage: node scripts/walkthrough.mjs <outDir> [kioskActivationCode]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const base = process.env.WEB_URL ?? "http://localhost:8080";
const outDir = process.argv[2] ?? "walkthrough";
const activationCode = process.argv[3] ?? process.env.KIOSK_CODE ?? "";
const language = process.env.LANG_CODE ?? "en";
const staffEmail = process.env.STAFF_EMAIL ?? "nurse01@demo.opdsathi.test";
const staffPassword = process.env.STAFF_PASSWORD ?? "";
if (!staffPassword) throw new Error("Set STAFF_PASSWORD (see DEMO_STAFF_PASSWORD in .env).");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  permissions: ["microphone"],
});
const page = await context.newPage();
const log = [];
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "warning") log.push(`[${m.type()}] ${m.text()}`);
});
page.on("pageerror", (e) => log.push(`[pageerror] ${e.message}`));
page.on("response", (r) => {
  if (r.status() >= 400) log.push(`[http ${r.status()}] ${r.request().method()} ${r.url()}`);
});

let step = 0;
const shot = async (name) => {
  step += 1;
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(outDir, `${String(step).padStart(2, "0")}-${name}.png`), fullPage: true });
  console.log(`shot ${step} ${name}`);
};

try {
  await page.goto(`${base}/kiosk`);
  await page.waitForLoadState("networkidle");
  await shot("welcome");
  if (activationCode && (await page.locator("#kiosk-activation-code").count())) {
    await page.fill("#kiosk-activation-code", activationCode);
    await page.getByRole("button", { name: /activate/i }).click();
    await page.waitForTimeout(800);
    await shot("activated");
  }
  if (process.env.STOP_AFTER === "welcome") throw new Error("stop");
  const languageNames = { hi: "à¤¹à¤¿à¤¨à¥à¤¦à¥€", mr: "à¤®à¤°à¤¾à¤ à¥€", en: "English" };
  await page.getByRole("button", { name: new RegExp(languageNames[language]) }).first().click().catch(() => {});
  const startButton = page.locator(".kiosk-welcome-cta, [data-testid=start-intake]").first();
  await startButton.click();
  await page.waitForURL(/intake/);
  await shot("consent");
  await page.locator(".intake-btn--primary").first().click();
  await page.waitForTimeout(700);
  for (let i = 0; i < 25; i += 1) {
    if (await page.locator(".intake-ticket").count()) break;
    if (await page.locator("#intake-age").count()) {
      await page.fill("#intake-age", "34");
      await page.locator(".intake-segmented button").first().click();
    }
    const chips = page.locator(".intake-chip:not(.is-none)");
    if (!(await chips.count()) && (await page.locator(".intake-chip.is-none").count()))
      await page.locator(".intake-chip.is-none").first().click();
    if (await chips.count()) {
      await chips.nth(0).click();
      if ((await chips.count()) > 2) await chips.nth(2).click().catch(() => {});
    }
    const scale = page.locator(".intake-scale-step");
    if (await scale.count()) await scale.nth(6).click();
    await shot(`step-${i}`);
    const next = page.locator(".intake-actions .intake-btn--primary:not([disabled])").last();
    if (!(await next.count())) {
      console.log("no enabled primary button; stopping");
      break;
    }
    await next.click();
    await page.waitForTimeout(1100);
  }
  await shot("final");
  const qr = page.locator(".intake-ticket-qr");
  if (await qr.count()) {
    const href = await qr.getAttribute("href");
    const phone = await context.newPage();
    await phone.setViewportSize({ width: 390, height: 844 });
    await phone.goto(new URL(href, base).toString());
    await phone.waitForTimeout(2500);
    await phone.screenshot({ path: join(outDir, "99-status-phone.png"), fullPage: true });
    console.log("shot status");
  }
  if (process.env.SKIP_STAFF !== "1") {
    await page.goto(`${base}/staff`);
    await page.waitForTimeout(1200);
    await shot("staff-login");
    const hospitalField = page.locator("#hospital-id");
    if ((await hospitalField.count()) && (await hospitalField.evaluate((el) => el.tagName)) === "INPUT")
      await hospitalField.fill(process.env.HOSPITAL_ID ?? "019b76da-a800-788a-852f-3dbb37d8bdd5");
    await page.fill("#staff-email", staffEmail);
    await page.fill("#staff-password", staffPassword);
    await page.locator("button[type=submit]").click();
    await page.waitForTimeout(2000);
    await shot("staff-home");
    // Open the newest visit's case sheet from the queue board, then visit each page.
    const open = page.getByRole("link", { name: /case sheet|open/i }).first();
    if (await open.count()) {
      await open.click();
      await page.waitForTimeout(2500);
      await shot("staff-case-sheet");
    }
    for (const path of (process.env.STAFF_PAGES ?? "queue,patients").split(",")) {
      await page.locator(`nav a[href^="/staff/${path}"]`).first().click().catch(() => {});
      await page.waitForTimeout(2000);
      await shot(`staff-${path}`);
    }
  }
} catch (error) {
  if (error.message !== "stop") {
    console.log("ERROR", error.message);
    await shot("error").catch(() => {});
  }
} finally {
  console.log(log.slice(0, 60).join("\n"));
  await browser.close();
}
