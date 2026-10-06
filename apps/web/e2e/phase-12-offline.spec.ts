import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const kioskToken = "synthetic.eyJzdWIiOiJraW9zay0xMjM0In0.signature";

test("offline short form stores encrypted data, warns on danger, and syncs once online", async ({ page, context }) => {
  await page.addInitScript((token) => {
    sessionStorage.setItem("osa.kiosk.access", token);
  }, kioskToken);
  let replayCount = 0;
  await page.route("**/intake/offline-submissions", async (route) => {
    replayCount += 1;
    const body = route.request().postDataJSON() as { idempotency_key: string; danger_tile_ids: string[] };
    expect(route.request().headers()["idempotency-key"]).toBe(body.idempotency_key);
    expect(body.danger_tile_ids).toContain("breathing_difficulty");
    await route.fulfill({
      status: 201,
      json: {
        confirmed: true,
        token: "GEN-123",
        status_token: "synthetic-status-token",
        status_url: "/s/synthetic-status-token",
        position: 8,
      },
    });
  });

  await page.goto("/kiosk/offline-intake");
  await context.setOffline(true);
  await page.getByLabel("Age in years").fill("37");
  await page.getByLabel("What would you like the doctor to know?").fill("Synthetic breathing concern");
  await page.getByLabel("Trouble breathing").check();
  await expect(page.getByRole("alert").filter({ hasText: "Please go to the nurse desk now." })).toBeVisible();
  await page.getByLabel("We will ask a few questions about your health and share your answers with the clinic team to help with your visit. This is not a diagnosis. You may ask staff for help or stop at any time. Do you agree to continue?").check();

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(accessibility.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);

  await page.getByRole("button", { name: "Save offline check-in" }).click();
  await expect(page.getByText("L-1234-001")).toBeVisible();
  await expect(page.getByText("Provisional — staff must confirm your real queue token.")).toBeVisible();
  const stored = await page.evaluate(async () => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("opd-saathi-offline-v1", 1);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const records = await new Promise<unknown[]>((resolve, reject) => {
      const request = database.transaction("submissions", "readonly").objectStore("submissions").getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    database.close();
    const typedRecords = records as Array<{ ciphertext: ArrayBuffer }>;
    return {
      count: typedRecords.length,
      containsPlaintext: new TextDecoder().decode(typedRecords[0]?.ciphertext).includes("Synthetic breathing concern"),
    };
  });
  expect(stored.count).toBe(1);
  expect(stored.containsPlaintext).toBe(false);

  await context.setOffline(false);
  await expect(page.getByText("Your real queue token is GEN-123. Please show this screen to clinic staff.")).toBeVisible({ timeout: 15_000 });
  expect(replayCount).toBe(1);
});
