// Render guide.html to docs/OPD-Saathi-Feature-Guide.pdf.
// Uses the Playwright installed for the web app: run from the repository root with
//   node docs/feature-guide-screenshots/render-pdf.mjs [preview-folder]
// Every section must fit one A4 page; the script stops if one would spill onto the next.
// With a preview folder, it also writes one PNG per page there for checking the layout.
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(
  new URL("../../apps/web/node_modules/playwright/index.mjs", import.meta.url).href
);
const source = new URL("./guide.html", import.meta.url);
const target = fileURLToPath(new URL("../OPD-Saathi-Feature-Guide.pdf", import.meta.url));
const previews = process.argv[2];

// A4 less the @page margins in guide.html (13 mm left and right, 13 mm top, 14 mm bottom).
const CONTENT_WIDTH = Math.round((184 / 25.4) * 96);
const CONTENT_HEIGHT = Math.floor((270 / 25.4) * 96);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: CONTENT_WIDTH, height: 1200 }, deviceScaleFactor: 1.5 });
await page.goto(source.href, { waitUntil: "load" });
await page.emulateMedia({ media: "print" });
await page.evaluate(() => document.fonts.ready);

const heights = await page.$$eval("section.page", (sections) =>
  sections.map((section) => Math.ceil(section.getBoundingClientRect().height)),
);
const over = heights
  .map((height, index) => ({ page: index + 1, height }))
  .filter(({ height }) => height > CONTENT_HEIGHT);
console.log(`${heights.length} sections; tallest ${Math.max(...heights)} px of ${CONTENT_HEIGHT} px`);

if (previews) {
  mkdirSync(previews, { recursive: true });
  const sections = await page.$$("section.page");
  for (const [index, section] of sections.entries()) {
    await section.screenshot({ path: join(previews, `page-${String(index + 1).padStart(2, "0")}.png`) });
  }
}
if (over.length) {
  console.error("These sections do not fit one A4 page:", over);
  await browser.close();
  process.exit(1);
}

await page.pdf({ path: target, preferCSSPageSize: true, printBackground: true, outline: true, tagged: true });
await browser.close();
console.log(`Wrote ${target}`);
