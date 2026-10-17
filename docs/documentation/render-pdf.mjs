// Render OPD_Saathi_Documentation.html to docs/OPD_Saathi_Documentation.pdf.
// Uses the Playwright installed for the web app: run from the repository root with
//   node docs/documentation/render-pdf.mjs
import { fileURLToPath } from "node:url";

const { chromium } = await import(
  new URL("../../apps/web/node_modules/playwright/index.mjs", import.meta.url).href
);
const source = new URL("./OPD_Saathi_Documentation.html", import.meta.url);
const target = fileURLToPath(new URL("../OPD_Saathi_Documentation.pdf", import.meta.url));

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(source.href, { waitUntil: "load" });
await page.evaluate(() => document.fonts.ready);
await page.pdf({
  path: target,
  preferCSSPageSize: true,
  printBackground: true,
  outline: true,
  tagged: true,
});
await browser.close();
console.log(`Wrote ${target}`);
