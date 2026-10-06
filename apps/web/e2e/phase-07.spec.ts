import AxeBuilder from "@axe-core/playwright";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const visitId = "00000000-0000-7000-8000-000000000701";
const departmentId = "00000000-0000-7000-8000-000000000710";
const hospitalId = "00000000-0000-7000-8000-000000000700";
const staffIds: Record<string, string> = {
  admin: "00000000-0000-7000-8000-000000000720",
  doctor: "00000000-0000-7000-8000-000000000721",
  nurse: "00000000-0000-7000-8000-000000000722",
};
const screenshots = resolve(
  process.cwd(),
  "..",
  "..",
  "docs",
  "screenshots",
  "phase-07",
);

const startingSheet = {
  schema_version: "1.0",
  language: "en",
  chief_complaint: {
    text_original: "Synthetic headache",
    text_en: "Synthetic headache",
    confidence: 0.9,
  },
  hpi: { associated_symptoms: [], severity_0_10: 4 },
  past_history: { conditions: [], surgeries: [] },
  medications: [],
  allergies: [],
  family_history: [],
  social: {},
  red_flags: [],
  routing: {
    department_code: "GENMED",
    confidence: 0.9,
    reason_en: "Synthetic route",
    needs_nurse_confirmation: false,
  },
  field_meta: {},
  disclaimer:
    "Collected by OPD Saathi from the patient's own statements. History only. Not a diagnosis.",
};

function userFor(role: "admin" | "doctor" | "nurse") {
  return {
    id: staffIds[role],
    name: `Synthetic ${role}`,
    email: `${role}@example.test`,
    role,
    hospital_id: hospitalId,
  };
}

async function mockStaffSession(
  page: Page,
  role: "admin" | "doctor" | "nurse",
) {
  await page.route("**/auth/refresh", (route) =>
    route.fulfill({
      json: {
        access_token: `synthetic-${role}-token`,
        token_type: "bearer",
        expires_in: 600,
        user: userFor(role),
      },
    }),
  );
}

async function runAxe(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const serious = result.violations.filter((violation) =>
    ["serious", "critical"].includes(violation.impact ?? ""),
  );
  console.log(`axe_serious_critical=${serious.length}`);
  expect(serious).toEqual([]);
}

function isQueueApiRequest(url: string) {
  const parsed = new URL(url);
  return parsed.pathname === "/queue";
}

async function saveScreenshot(page: Page, name: string) {
  mkdirSync(screenshots, { recursive: true });
  await page.screenshot({ path: resolve(screenshots, name), fullPage: true });
}

test("doctor edit conflicts merge across two browsers, exports PDF, and prints the sheet", async ({
  browser,
}) => {
  const shared = { version: 1, caseSheet: structuredClone(startingSheet) };
  const firstContext = await browser.newContext({ acceptDownloads: true });
  const secondContext = await browser.newContext();
  const first = await firstContext.newPage();
  const second = await secondContext.newPage();

  async function setupDoctor(page: Page) {
    await mockStaffSession(page, "doctor");
    await page.route(`**/visits/${visitId}/transcript`, (route) =>
      route.fulfill({ json: { visit_id: visitId, turns: [] } }),
    );
    await page.route(`**/visits/${visitId}/case-sheet.pdf`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/pdf",
        body: "%PDF-1.7 synthetic PDF",
      }),
    );
    await page.route(`**/visits/${visitId}/case-sheet`, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          json: {
            visit_id: visitId,
            version: shared.version,
            case_sheet: shared.caseSheet,
          },
          headers: { ETag: `"${shared.version}"` },
        });
        return;
      }
      const expectedVersion = Number(
        route.request().headers()["if-match"]?.replaceAll('"', ""),
      );
      if (expectedVersion !== shared.version) {
        await route.fulfill({
          status: 409,
          contentType: "application/problem+json",
          body: JSON.stringify({ detail: "Case sheet changed" }),
        });
        return;
      }
      const operations = route.request().postDataJSON() as {
        path: string;
        value: string;
      }[];
      for (const operation of operations) {
        if (operation.path === "/chief_complaint/text_original") {
          shared.caseSheet.chief_complaint.text_original = operation.value;
          shared.caseSheet.chief_complaint.text_en = operation.value;
        }
      }
      shared.version += 1;
      await route.fulfill({
        json: {
          visit_id: visitId,
          version: shared.version,
          case_sheet: shared.caseSheet,
        },
        headers: { ETag: `"${shared.version}"` },
      });
    });
  }

  await setupDoctor(first);
  await setupDoctor(second);
  await first.goto(`/staff/consultations?visit_id=${visitId}&status=called`);
  await second.goto(`/staff/consultations?visit_id=${visitId}&status=called`);
  await expect(first.getByText("Synthetic headache").first()).toBeVisible();
  await expect(second.getByText("Synthetic headache").first()).toBeVisible();
  await runAxe(first);

  await first.getByRole("button", { name: "Edit" }).first().click();
  await first
    .getByRole("textbox", { name: "Chief complaint" })
    .fill("Synthetic updated headache");
  await first.getByRole("button", { name: "Save edit" }).click();
  await expect(first.getByText("Synthetic updated headache")).toBeVisible();

  await second.getByRole("button", { name: "Edit" }).first().click();
  await second
    .getByRole("textbox", { name: "Chief complaint" })
    .fill("Synthetic second-browser edit");
  await second.getByRole("button", { name: "Save edit" }).click();
  await expect(second.getByRole("alertdialog")).toBeVisible();
  await second.getByRole("button", { name: "Apply my edit to latest" }).click();
  await expect(second.getByText("Synthetic second-browser edit")).toBeVisible();
  expect(shared.version).toBe(3);

  const downloadPromise = first.waitForEvent("download");
  await first.getByRole("button", { name: "Download case sheet PDF" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain(visitId);
  await first.emulateMedia({ media: "print" });
  await expect(first.locator(".consult-sheet")).toBeVisible();
  await expect(first.locator(".consult-transcript")).toBeHidden();
  await expect(first.locator(".consult-header")).toBeHidden();
  await saveScreenshot(first, "doctor-consultation-print.png");

  await firstContext.close();
  await secondContext.close();
});

test("nurse assisted registration presents consent before collecting identity", async ({
  page,
}) => {
  await mockStaffSession(page, "nurse");
  let identitySubmissions = 0;
  await page.route("**/intake/assisted-sessions", (route) =>
    route.fulfill({
      json: {
        visit_id: visitId,
        patient_access_token: "synthetic-patient-token",
        assisted: true,
        consent: {
          language: "en",
          version: "synthetic-v1",
          sha256: "synthetic-hash",
          text: "Synthetic consent text for the test patient.",
        },
      },
    }),
  );
  await page.route(`**/intake/sessions/${visitId}/consent`, (route) =>
    route.fulfill({ json: { next: "identity" } }),
  );
  await page.route(`**/intake/sessions/${visitId}/identity`, (route) => {
    identitySubmissions += 1;
    return route.fulfill({ json: { next: "danger" } });
  });

  await page.goto("/staff/patients");
  await expect(
    page.getByRole("heading", { name: "Nurse console" }),
  ).toBeVisible();
  await runAxe(page);
  await page
    .getByRole("button", { name: "Review consent with patient" })
    .click();
  await expect(
    page.getByText("Synthetic consent text for the test patient."),
  ).toBeVisible();
  await expect(page.getByLabel("Patient name")).toHaveCount(0);
  await saveScreenshot(page, "nurse-consent-before-identity.png");
  await page
    .getByRole("button", { name: "I agree and want to continue" })
    .click();
  await expect(page.getByLabel("Patient name")).toBeVisible();
  expect(identitySubmissions).toBe(0);
  await saveScreenshot(page, "nurse-identity-after-consent.png");
});

test("administrator creates a kiosk and dismisses its one-time activation code", async ({
  page,
}) => {
  await mockStaffSession(page, "admin");
  await page.route("**/admin/**", async (route) => {
    const request = route.request();
    if (
      request.method() === "POST" &&
      request.url().endsWith("/admin/kiosks")
    ) {
      await route.fulfill({
        status: 201,
        json: {
          id: "00000000-0000-7000-8000-000000000730",
          label: "Synthetic reception kiosk",
          activation_code: "synthetic-one-time-activation",
        },
      });
      return;
    }
    await route.fulfill({ json: { items: [], next_cursor: null } });
  });

  await page.goto("/staff/settings");
  await expect(
    page.getByRole("heading", { name: "Administration" }),
  ).toBeVisible();
  await runAxe(page);
  await page.getByRole("tab", { name: "Kiosks" }).click();
  await page.getByLabel("Kiosk label").fill("Synthetic reception kiosk");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("synthetic-one-time-activation")).toBeVisible();
  await saveScreenshot(page, "admin-kiosk-one-time-code.png");
  await page.getByRole("button", { name: "I copied the code" }).click();
  await expect(page.getByText("synthetic-one-time-activation")).toHaveCount(0);
  await saveScreenshot(page, "admin-kiosk-code-dismissed.png");
});

test("doctor and nurse routes stay inside their role access map", async ({
  browser,
}) => {
  for (const role of ["doctor", "nurse"] as const) {
    const page = await browser.newPage();
    await mockStaffSession(page, role);
    await page.route(
      (url) => isQueueApiRequest(url.href),
      (route) =>
        route.fulfill({
          json: {
            items: [],
            departments: [],
            next_cursor: null,
            limit: 50,
          },
        }),
    );
    await page.route("**/alerts**", (route) =>
      route.fulfill({ json: { items: [] } }),
    );
    await page.route("**/admin/**", (route) =>
      route.fulfill({ status: 403, json: { detail: "Forbidden" } }),
    );
    await page.goto("/staff/settings");
    await expect(page).toHaveURL(/\/staff\/queue$/);
    await expect(page.getByRole("heading", { name: "Queue" })).toBeVisible();
    await expect(page.getByRole("tablist")).toHaveCount(0);
    await page.close();
  }
});

test("5,000-row virtual queue applies a live urgent update in under 100 ms", async ({
  page,
}) => {
  await mockStaffSession(page, "doctor");
  const queueItems = Array.from({ length: 5000 }, (_, index) => ({
    visit_id: `00000000-0000-7000-8000-${String(index).padStart(12, "0")}`,
    department_id: departmentId,
    token: `GEN-${String(index + 1).padStart(4, "0")}`,
    urgency: "routine",
    status: "queued",
    age_years: 40,
    sex: "unknown",
    complaint: "Synthetic large queue complaint",
    position: index + 1,
    queued_at: "2026-10-06T08:00:00+05:30",
  }));
  await page.route(
    (url) => isQueueApiRequest(url.href),
    (route) =>
      route.fulfill({
        json: {
          items: queueItems,
          departments: [
            { id: departmentId, code: "GENMED", name: "General Medicine" },
          ],
          next_cursor: null,
          limit: 50,
        },
      }),
  );
  await page.route("**/alerts**", (route) =>
    route.fulfill({ json: { items: [] } }),
  );

  let queueSocket: import("@playwright/test").WebSocketRoute | undefined;
  let resolveAuthenticated!: () => void;
  const authenticated = new Promise<void>((resolve) => {
    resolveAuthenticated = resolve;
  });
  await page.routeWebSocket(
    (url) =>
      url.pathname === "/ws/staff/queue" &&
      !url.searchParams.has("alerts_only"),
    (socket) => {
      queueSocket = socket;
      socket.onMessage((data) => {
        const message = JSON.parse(String(data)) as { type?: string };
        if (message.type === "auth") {
          resolveAuthenticated();
          socket.send(JSON.stringify({ type: "queue.snapshot", seq: 1 }));
        }
      });
    },
  );
  await page.routeWebSocket(/\/ws\/staff\/alerts/, () => undefined);

  await page.goto("/staff/queue");
  await expect(
    page.getByRole("region", { name: "Queue patients" }),
  ).toBeVisible();
  await expect(page.locator(".staff-queue-item").first()).toBeVisible();
  const renderedRows = await page.locator(".staff-queue-item").count();
  expect(renderedRows).toBeLessThan(50);

  await page.evaluate(() => {
    (
      window as typeof window & { queueUpdateStarted?: number }
    ).queueUpdateStarted = performance.now();
  });
  await authenticated;
  expect(queueSocket).toBeDefined();
  queueSocket?.send(
    JSON.stringify({
      type: "queue.delta",
      seq: 2,
      ops: [
        {
          op: "upsert",
          item: {
            visit_id: queueItems[0].visit_id,
            token: "GEN-0001",
            urgency: "urgent",
            status: "queued",
          },
        },
      ],
    }),
  );
  await expect(page.locator(".staff-queue-item--urgent strong")).toHaveText(
    "GEN-0001",
  );
  const elapsed = await page.evaluate(
    () =>
      performance.now() -
      (window as typeof window & { queueUpdateStarted?: number })
        .queueUpdateStarted!,
  );
  console.log(`queue_urgent_update_ms=${elapsed.toFixed(2)}`);
  expect(elapsed).toBeLessThan(100);
});
