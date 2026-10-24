import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// The full privacy notice for patients (Phase 7, 7.5.2), synthetic data only: readable on a
// small phone in each language, accessible, and honest about a draft.
const HOSPITAL = "00000000-0000-7000-8000-000000000f01";
const TEXT = {
  en: "## Who is responsible\n\nThis hospital is responsible for your information.\n\n## Your choices and rights\n\n- Stop online AI help at any time.\n- Ask to see your information.",
  hi: "## कौन ज़िम्मेदार है\n\nआपकी जानकारी की ज़िम्मेदारी इस अस्पताल की है।\n\n## आपके विकल्प और अधिकार\n\n- ऑनलाइन AI सहायता कभी भी बंद करें।\n- अपनी जानकारी दिखाने के लिए कहें।",
};

test("a patient reads the privacy notice on a small phone, in Hindi too", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.route(`**/privacy-notice/${HOSPITAL}?language=*`, (route) => {
    const language = new URL(route.request().url()).searchParams.get("language") === "hi" ? "hi" : "en";
    return route.fulfill({
      json: {
        hospital_name: "Synthetic District Hospital",
        grievance_contact: "Dr S. Rao, Grievance Officer · Room 12 · 020-2555-0101",
        notice: {
          language,
          version: "privacy-notice-1.0",
          review_status: "draft_needs_clinician_review",
          text: TEXT[language],
        },
      },
    });
  });

  await page.goto(`/privacy/${HOSPITAL}?lang=en`);
  await expect(page.getByRole("heading", { name: "Privacy notice" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Who is responsible" })).toBeVisible();
  await expect(page.getByRole("note")).toContainText("Draft");
  await expect(page.getByText("Dr S. Rao, Grievance Officer")).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
  // Nothing wider than the phone.
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);

  await page.getByRole("button", { name: "हिन्दी" }).click();
  await expect(page.getByRole("heading", { name: "कौन ज़िम्मेदार है" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "गोपनीयता सूचना" })).toBeVisible();
});
