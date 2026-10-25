// Renders the PNG app icons from the SVGs in public/. Run it after changing saathi-icon.svg or
// saathi-icon-maskable.svg: node scripts/render-icons.mjs (needs Playwright's Chromium).
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const ICONS = [
  { source: "saathi-icon.svg", target: "saathi-icon-192.png", size: 192 },
  { source: "saathi-icon.svg", target: "saathi-icon-512.png", size: 512 },
  {
    source: "saathi-icon-maskable.svg",
    target: "saathi-icon-maskable-512.png",
    size: 512,
  },
  // iOS rounds the corners itself, so its home-screen icon is the full square.
  {
    source: "saathi-icon-maskable.svg",
    target: "apple-touch-icon.png",
    size: 180,
  },
];

const publicDir = new URL("../public/", import.meta.url);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  for (const { source, target, size } of ICONS) {
    const svg = await readFile(new URL(source, publicDir));
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(
      `<body style="margin:0"><img width="${size}" height="${size}" style="display:block" ` +
        `src="data:image/svg+xml;base64,${svg.toString("base64")}"></body>`,
    );
    await page.screenshot({
      path: fileURLToPath(new URL(target, publicDir)),
      omitBackground: true,
    });
    console.log(`public/${target}`);
  }
} finally {
  await browser.close();
}
