// Builds docs/showcase/OPD-Saathi-Showcase.html: one self-contained file (screens, fonts and
// script inside it) that opens from a USB stick with no network.
//
//   node docs/showcase/build.mjs            inline img/ and the fonts into showcase.template.html
//   node docs/showcase/build.mjs shots DIR  first turn the raw PNG captures in DIR into img/*.webp
//
// The screens are real: captured from the running demo stack with synthetic patients. Run with
// the Node that apps/web uses; Playwright (for resizing) and the fonts come from apps/web.
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "..", "..", "apps", "web");
const img = path.join(here, "img");

// name in the page -> [raw capture, width in the page's file]
const SHOTS = {
  "k-welcome": ["kiosk-02-welcome-lang", 1440],
  "k-consent": ["kiosk-03-consent", 1440],
  "k-ai": ["kiosk-04-ai-consent", 1440],
  "k-details": ["kiosk-05-identity", 1440],
  "k-card": ["kiosk-07-opd-card", 1440],
  "k-followup": ["kiosk-08-follow-up", 1440],
  "k-danger": ["kiosk-09-danger", 1440],
  "k-question": ["kiosk-10-q01", 1440],
  "k-listening": ["kiosk-11-voice-listening", 1440],
  "k-heard": ["kiosk-12-voice-heard", 1440],
  "k-adaptive": ["kiosk-17-q04-picked", 1440],
  "k-scale": ["kiosk-15-q03", 1440],
  "k-review": ["kiosk-23-review", 1440],
  "k-papers": ["kiosk-24-papers", 1440],
  "k-token": ["kiosk-25-token", 1440],
  "p-poster": ["admin-20-checkin-qr", 1600],
  "p-landing": ["phone-02-landing-hindi", 780],
  "p-ai": ["phone-04-ai-consent", 780],
  "p-question": ["phone-08-q01", 780],
  "p-picked": ["phone-11-q02-picked", 780],
  "p-review": ["phone-16-review", 780],
  "p-token": ["phone-17-token", 780],
  "p-waiting": ["phone-18-status-waiting", 780],
  "p-called": ["status-02-called", 780],
  "e-danger": ["emergency-07-danger-selected", 1440],
  "n-board": ["nurse-01-board", 1600],
  "n-why": ["nurse-03-why", 1600],
  "d-rail": ["doctor-01-rail", 1600],
  "d-overview": ["doctor-03-overview", 1600],
  "d-source": ["doctor-05-source-transcript", 1600],
  "d-accepted": ["doctor-06-brief-accepted", 1600],
  "d-previous": ["doctor-08-previous-visits", 1600],
  "d-papers": ["doctor-11-paper-accepted", 1600],
  "d-notes": ["doctor-14-notes", 1600],
  "a-reports": ["admin-01-reports", 1600],
  "a-audit": ["admin-07-audit", 1600],
  "a-settings": ["admin-05-departments", 1600],
};

async function shots(rawDir) {
  const require = createRequire(path.join(web, "package.json"));
  const { chromium } = require("playwright");
  mkdirSync(img, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage();
  let total = 0;
  for (const [name, [raw, width]] of Object.entries(SHOTS)) {
    const file = path.join(rawDir, `${raw}.png`);
    if (!existsSync(file)) {
      console.log(`missing ${raw}.png`);
      continue;
    }
    const source = `data:image/png;base64,${readFileSync(file).toString("base64")}`;
    const data = await page.evaluate(
      async ({ source, width }) => {
        const image = new Image();
        image.src = source;
        await image.decode();
        const scale = Math.min(1, width / image.naturalWidth);
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(image.naturalWidth * scale);
        canvas.height = Math.round(image.naturalHeight * scale);
        const context = canvas.getContext("2d");
        context.imageSmoothingQuality = "high";
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        return canvas.toDataURL("image/webp", 0.82);
      },
      { source, width },
    );
    const bytes = Buffer.from(data.split(",")[1], "base64");
    writeFileSync(path.join(img, `${name}.webp`), bytes);
    total += bytes.length;
  }
  await browser.close();
  console.log(`${Object.keys(SHOTS).length} screens, ${(total / 1e6).toFixed(1)} MB in img/`);
}

function build() {
  const font = (file) =>
    readFileSync(path.join(web, "node_modules", "@fontsource-variable", file)).toString("base64");
  const fonts = `
@font-face{font-family:"Inter";font-style:normal;font-weight:100 900;font-display:swap;
  src:url(data:font/woff2;base64,${font("inter/files/inter-latin-wght-normal.woff2")}) format("woff2")}
@font-face{font-family:"Noto Sans Devanagari";font-style:normal;font-weight:100 900;font-display:swap;
  src:url(data:font/woff2;base64,${font("noto-sans-devanagari/files/noto-sans-devanagari-devanagari-wght-normal.woff2")}) format("woff2")}`;
  const images = {};
  for (const file of readdirSync(img).filter((name) => name.endsWith(".webp")).sort()) {
    images[file.replace(".webp", "")] =
      `data:image/webp;base64,${readFileSync(path.join(img, file)).toString("base64")}`;
  }
  const missing = Object.keys(SHOTS).filter((name) => !images[name]);
  if (missing.length) throw new Error(`img/ lacks: ${missing.join(", ")} (run "build.mjs shots DIR")`);
  const template = readFileSync(path.join(here, "showcase.template.html"), "utf8");
  for (const mark of ["/*__FONTS__*/", '"__SHOTS__"']) {
    if (!template.includes(mark)) throw new Error(`the template lacks ${mark}`);
  }
  const html = template
    .replace("/*__FONTS__*/", () => fonts)
    .replace('"__SHOTS__"', () => JSON.stringify(images));
  const out = path.join(here, "OPD-Saathi-Showcase.html");
  writeFileSync(out, html);
  console.log(`${out}: ${(Buffer.byteLength(html) / 1e6).toFixed(1)} MB, ${Object.keys(images).length} screens`);
}

if (process.argv[2] === "shots") await shots(path.resolve(process.argv[3] ?? "."));
else build();
