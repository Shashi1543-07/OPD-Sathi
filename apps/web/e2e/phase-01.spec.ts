import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

const viewports = [
  { name: "kiosk-landscape", width: 1280, height: 800 },
  { name: "tablet-portrait", width: 768, height: 1024 },
  { name: "phone", width: 390, height: 844 },
];
const languages = ["en", "hi", "mr"];
const screens = [
  { name: "welcome", path: "/kiosk", kiosk: true },
  { name: "s2-consent", path: "/kiosk/preview/s2", kiosk: true },
  { name: "s3-concerns", path: "/kiosk/preview/s3", kiosk: true },
  { name: "s4-details", path: "/kiosk/preview/s4", kiosk: true },
  { name: "s5-interview", path: "/kiosk/preview/s5", kiosk: true },
  { name: "s6-summary", path: "/kiosk/preview/s6", kiosk: true },
  { name: "s7-token", path: "/kiosk/preview/s7", kiosk: true },
  { name: "staff-login", path: "/staff", kiosk: false },
  { name: "patient-status", path: "/s/demo", kiosk: false },
];
const screenshotDir = path.resolve(
  "..",
  "..",
  "docs",
  "screenshots",
  "phase-01",
);
mkdirSync(screenshotDir, { recursive: true });

for (const screen of screens) {
  for (const viewport of viewports) {
    for (const language of languages) {
      test(`${screen.name} / ${language} / ${viewport.name}: screenshot, axe, layout`, async ({
        page,
      }) => {
        await page.setViewportSize({
          width: viewport.width,
          height: viewport.height,
        });
        await page.goto(`${screen.path}?lang=${language}`);
        await expect(page.locator("h1")).toBeVisible();

        const horizontalOverflow = await page.evaluate(
          () => document.documentElement.scrollWidth > window.innerWidth,
        );
        expect(
          horizontalOverflow,
          "page should not overflow horizontally",
        ).toBe(false);

        if (screen.kiosk) {
          const smallTargets = await page
            .locator(".kiosk-app a, .kiosk-app button, .kiosk-app input")
            .evaluateAll((elements) =>
              elements.flatMap((element) => {
                const rect = element.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return [];
                return rect.width < 64 || rect.height < 64
                  ? [
                      `${element.tagName.toLowerCase()} ${element.textContent?.trim() ?? ""}: ${Math.round(rect.width)}×${Math.round(rect.height)}`,
                    ]
                  : [];
              }),
            );
          expect(
            smallTargets,
            "visible kiosk targets must be at least 64×64 CSS px",
          ).toEqual([]);
        }

        const accessibility = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
          .analyze();
        expect(
          accessibility.violations.map(
            ({ id, impact, description, nodes }) => ({
              id,
              impact,
              description,
              nodes: nodes.map((node) => node.target),
            }),
          ),
        ).toEqual([]);

        await page.screenshot({
          path: path.join(
            screenshotDir,
            `${screen.name}--${language}--${viewport.name}.png`,
          ),
          fullPage: true,
          animations: "disabled",
        });
      });
    }
  }
}

for (const viewport of viewports) {
  for (const screen of [
    { name: "welcome", path: "/kiosk" },
    { name: "s3-concerns", path: "/kiosk/preview/s3" },
    { name: "staff-login", path: "/staff" },
    { name: "patient-status", path: "/s/demo" },
  ]) {
    test(`${screen.name} / pseudo-locale / ${viewport.name}: no clipped page`, async ({
      page,
    }) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await page.goto(`${screen.path}?lang=xa`);
      await expect(page.locator("h1")).toBeVisible();
      const overflow = await page.evaluate(() => ({
        horizontal: document.documentElement.scrollWidth > window.innerWidth,
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
      }));
      expect(overflow.horizontal, JSON.stringify(overflow)).toBe(false);
    });
  }
}
