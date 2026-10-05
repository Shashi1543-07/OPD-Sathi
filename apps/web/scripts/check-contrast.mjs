import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const tokensPath = fileURLToPath(
  new URL("../src/styles/tokens.css", import.meta.url),
);
const stylesheet = await readFile(tokensPath, "utf8");
const tokens = new Map(
  [...stylesheet.matchAll(/^\s*--([\w-]+):\s*(#[\da-f]{6})\s*;/gim)].map(
    (match) => [match[1], match[2]],
  ),
);

const documentedPairs = [
  ["Main text on app background", "navy-900", "bg"],
  ["Main text on cards", "navy-900", "surface"],
  ["Secondary text on app background", "slate-600", "bg"],
  ["Secondary text on cards", "slate-600", "surface"],
  ["Primary button label", "surface", "primary"],
  ["Primary link", "primary", "surface"],
  ["Text on primary highlight", "navy-900", "primary-tint"],
  ["Urgent status label", "urgent", "urgent-tint"],
  ["Priority status label", "priority-ink", "priority-tint"],
  ["Routine status label", "routine-ink", "routine-tint"],
  ["Info and focus label on app background", "info", "bg"],
  ["Info and focus label on cards", "info", "surface"],
];

function luminance(hex) {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    .map((channel) => parseInt(channel, 16) / 255);
  const linear = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );

  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

let failed = false;

for (const [label, foregroundName, backgroundName] of documentedPairs) {
  const foreground = tokens.get(foregroundName);
  const background = tokens.get(backgroundName);

  if (!foreground || !background) {
    console.error(
      `Missing color token for ${label}: ${foregroundName}/${backgroundName}`,
    );
    failed = true;
    continue;
  }

  const values = [luminance(foreground), luminance(background)].sort(
    (a, b) => b - a,
  );
  const ratio = (values[0] + 0.05) / (values[1] + 0.05);
  const status = ratio >= 4.5 ? "PASS" : "FAIL";

  console.log(
    `${status} ${label}: ${ratio.toFixed(2)}:1 (${foreground} on ${background})`,
  );
  if (ratio < 4.5) failed = true;
}

if (failed) {
  console.error(
    "Contrast check failed: every text pair must reach WCAG AA 4.5:1.",
  );
  process.exitCode = 1;
} else {
  console.log(
    `Contrast check passed for ${documentedPairs.length} documented text pairs.`,
  );
}
