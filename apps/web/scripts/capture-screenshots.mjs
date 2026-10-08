import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const baseUrl = process.env.SCREENSHOT_BASE_URL ?? "http://127.0.0.1:5173";
const output = fileURLToPath(new URL("../../../docs/screenshots/phase-14/", import.meta.url));
await mkdir(output, { recursive: true });

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(`${baseUrl}/kiosk?demo=1`, { waitUntil: "networkidle" });
  await page.screenshot({ path: join(output, "judge-kiosk-en.png"), fullPage: true });

  for (const language of ["hi", "mr"]) {
    await page.locator(`.kiosk-language-selector button[lang="${language}"]`).click();
    await page.screenshot({
      path: join(output, `judge-kiosk-${language}.png`),
      fullPage: true,
    });
  }
} finally {
  await browser.close();
}
