import { mkdir, readFile, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import lighthouse from "lighthouse";
import { launch as launchChrome } from "chrome-launcher";
import { chromium } from "@playwright/test";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(scriptDir, "..");
const config = JSON.parse(
  await readFile(path.join(webDir, "lighthouserc.json"), "utf8"),
);
const outputDir = path.resolve(webDir, config.ci.upload.outputDir);
const port = 4174;
const origin = `http://127.0.0.1:${port}`;
const preview = spawn(
  process.execPath,
  [
    path.join(webDir, "node_modules/vite/bin/vite.js"),
    "preview",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--strictPort",
  ],
  {
    cwd: webDir,
    stdio: ["ignore", "pipe", "pipe"],
  },
);

let previewOutput = "";
preview.stdout.on("data", (chunk) => {
  previewOutput += chunk;
});
preview.stderr.on("data", (chunk) => {
  previewOutput += chunk;
});

async function waitForPreview() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (preview.exitCode !== null)
      throw new Error(`Vite preview exited early.\n${previewOutput}`);
    try {
      const response = await fetch(`${origin}/kiosk`);
      if (response.ok) return;
    } catch {
      // The preview server is still starting.
    }
    await delay(500);
  }
  throw new Error(`Timed out waiting for Vite preview.\n${previewOutput}`);
}

const assertions = config.ci.assert.assertions;
const categories = config.ci.collect.settings.onlyCategories;
const runs = config.ci.collect.numberOfRuns;
const failures = [];
const summaries = [];

await mkdir(outputDir, { recursive: true });

try {
  await waitForPreview();
  for (const configuredUrl of config.ci.collect.url) {
    const route = new URL(configuredUrl).pathname;
    for (let run = 1; run <= runs; run += 1) {
      const chrome = await launchChrome({
        chromePath: process.env.CHROME_PATH ?? chromium.executablePath(),
        chromeFlags: config.ci.collect.settings.chromeFlags,
        startingUrl: "about:blank",
      });
      try {
        const result = await lighthouse(`${origin}${route}`, {
          port: chrome.port,
          output: "json",
          onlyCategories: categories,
          logLevel: "error",
        });
        if (!result) throw new Error("Lighthouse returned no report.");
        const report = result.lhr;
        const slug =
          route === "/" ? "root" : route.slice(1).replaceAll("/", "-");
        await writeFile(
          path.join(outputDir, `${slug}-run-${run}.json`),
          JSON.stringify(report),
        );

        const categoryScores = Object.fromEntries(
          categories.map((category) => [
            category,
            report.categories[category].score,
          ]),
        );
        const cls = report.audits["cumulative-layout-shift"].numericValue;
        summaries.push({ route, run, scores: categoryScores, cls });
        console.log(
          `${route} run ${run}: ${categories.map((category) => `${category} ${Math.round(categoryScores[category] * 100)}`).join(", ")}, CLS ${cls.toFixed(4)}`,
        );

        for (const [assertion, [, threshold]] of Object.entries(assertions)) {
          if (assertion.startsWith("categories:")) {
            const category = assertion.slice("categories:".length);
            const score = report.categories[category]?.score;
            if (typeof score !== "number" || score < threshold.minScore) {
              failures.push(
                `${route} run ${run}: ${assertion} score ${score} is below ${threshold.minScore}.`,
              );
            }
          } else if (
            assertion === "cumulative-layout-shift" &&
            cls > threshold.maxNumericValue
          ) {
            failures.push(
              `${route} run ${run}: CLS ${cls} exceeds ${threshold.maxNumericValue}.`,
            );
          }
        }
      } finally {
        await chrome.kill();
      }
    }
  }
  await writeFile(
    path.join(outputDir, "summary.json"),
    JSON.stringify(summaries, null, 2),
  );
} finally {
  preview.kill("SIGTERM");
}

if (failures.length > 0) {
  console.error(
    "\nLighthouse thresholds failed:\n" +
      failures.map((failure) => `- ${failure}`).join("\n"),
  );
  process.exitCode = 1;
} else {
  console.log(
    `\nAll Lighthouse thresholds passed across ${summaries.length} runs.`,
  );
}
