import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { devices, expect, test, type Page } from "@playwright/test";

// A patient's own phone, from the hospital's check-in QR poster (synthetic data only).
const phone = devices["Pixel 7"];
test.use({
  viewport: phone.viewport,
  userAgent: phone.userAgent,
  deviceScaleFactor: phone.deviceScaleFactor,
  isMobile: true,
  hasTouch: true,
});

const CODE = "C".repeat(43);
const STATUS = "S".repeat(43);
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function copy(namespace: string) {
  return JSON.parse(
    readFileSync(resolve(process.cwd(), "src", "locales", "en", `${namespace}.json`), "utf8"),
  ) as Record<string, string>;
}
const intake = copy("intake");
const kiosk = copy("kiosk");

/** A plain http://<LAN address> page: the browser offers no crypto.randomUUID there. */
async function withoutSecureContextIds(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(Crypto.prototype, "randomUUID", { value: undefined, configurable: true });
  });
}

async function installApiMocks(page: Page) {
  const questions = [
    {
      id: "chief_concern",
      prompt: "What is the main problem?",
      input: "text",
      optional: false,
      multi: true,
      options: [{ id: "fever", label: "Fever", none: false }],
    },
    { id: "severity", prompt: "How bad is it?", input: "scale", optional: true, multi: false, options: [] },
  ];
  const answered: { id: string; prompt: string; answer: string | number | null }[] = [];
  const state = { index: 0, sessionsStarted: 0, idempotencyKeys: [] as string[] };
  const current = () =>
    state.index < questions.length
      ? {
          complete: false,
          question: questions[state.index],
          progress: { current: state.index + 1, total: questions.length },
          answered: [...answered],
        }
      : {
          complete: true,
          readback: "You have fever.",
          answered: [...answered],
          case_sheet: { red_flags: [] },
        };

  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/intake/capabilities", (route) =>
    route.fulfill({ json: { stt: "browser", tts: "browser", ai: false } }),
  );
  await page.route(`**/intake/checkin/${CODE}`, (route) =>
    route.fulfill({ json: { hospital_name: "Synthetic District Hospital", languages: ["hi", "mr", "en"] } }),
  );
  await page.route(`**/intake/checkin/${CODE}/sessions`, async (route) => {
    state.sessionsStarted += 1;
    await route.fulfill({
      status: 201,
      json: {
        visit_id: "00000000-0000-7000-8000-00000000a001",
        patient_access_token: "patient-phone-token",
        expires_in: 7200,
        channel: "mobile",
        hospital_name: "Synthetic District Hospital",
        consent: {
          language: "en",
          version: "consent-test",
          sha256: "a".repeat(64),
          text: "We ask about your health to help with your visit. This is not a diagnosis.",
        },
      },
    });
  });
  await page.route("**/intake/sessions/*/consent", (route) =>
    route.fulfill({ json: { granted: true, next: "identity" } }),
  );
  await page.route("**/intake/sessions/*/identity", (route) =>
    route.fulfill({ json: { next: "danger_signs" } }),
  );
  await page.route("**/intake/sessions/*/danger-tiles", (route) =>
    route.fulfill({ json: { tiles: [{ id: "chest_pain", label: "Chest pain" }] } }),
  );
  await page.route("**/intake/sessions/*/danger-signs", (route) =>
    route.fulfill({ json: { staff_help_required: false } }),
  );
  await page.route("**/intake/sessions/*/interview/next-question", (route) =>
    route.fulfill({ json: current() }),
  );
  await page.route("**/intake/sessions/*/interview/answers", async (route) => {
    state.idempotencyKeys.push(route.request().headers()["idempotency-key"] ?? "");
    const payload = JSON.parse(route.request().postData() ?? "{}");
    const question = questions[state.index];
    answered.push({ id: question.id, prompt: question.prompt, answer: payload.skip ? null : payload.answer });
    state.index += 1;
    await route.fulfill({ json: current() });
  });
  await page.route("**/intake/sessions/*/confirm", async (route) => {
    state.idempotencyKeys.push(route.request().headers()["idempotency-key"] ?? "");
    await route.fulfill({
      json: {
        confirmed: true,
        status: "queued",
        token: "MED-012",
        status_token: STATUS,
        status_url: `/s/${STATUS}`,
        position: 3,
        department_name: "General Medicine",
        wait_minutes: 8,
        opd_card: { number: "26-000012", pin: "482913" },
      },
    });
  });
  const status = {
    state: "waiting",
    token: "MED-012",
    position: 3,
    eta_min: 5,
    eta_max: 10,
    department: { en: "General Medicine" },
    hospital: "Synthetic District Hospital",
    language: "en",
  };
  await page.route(`**/status/${STATUS}`, (route) => route.fulfill({ json: status }));
  await page.route(`**/status/${STATUS}/stream`, (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: `event: status\ndata: ${JSON.stringify(status)}\n\n`,
    }),
  );
  return state;
}

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

test("a patient checks in on their own phone over plain HTTP and keeps their token", async ({
  page,
}) => {
  await withoutSecureContextIds(page);
  const api = await installApiMocks(page);

  await page.goto(`/m/${CODE}`);
  // The header hides the hospital name at phone width; the welcome card shows it.
  await expect(page.locator(".welcome-card").getByText("Synthetic District Hospital")).toBeVisible();
  await expect(page.getByRole("heading", { name: kiosk.mobileWelcomeTitle })).toBeVisible();
  await expectNoHorizontalScroll(page);
  const welcomeA11y = await new AxeBuilder({ page }).analyze();
  expect(
    welcomeA11y.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);

  await page.getByRole("button", { name: "English" }).first().click();
  await page.getByRole("button", { name: kiosk.mobileStart }).click();
  await page.getByRole("button", { name: intake.consentAgree }).click();
  await page.getByLabel(intake.ageQuestion).fill("41");
  await page.getByRole("radio", { name: intake.sexMale, exact: true }).click();
  await page.getByRole("button", { name: intake.next, exact: true }).click();
  await page.getByRole("button", { name: intake.returningNo }).click();
  await page.getByRole("button", { name: intake.dangerNone }).click();

  await expect(page.getByRole("heading", { name: "What is the main problem?" })).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.getByRole("button", { name: "Fever" }).click();
  await page.getByRole("button", { name: intake.next, exact: true }).click();
  await page.getByRole("radio", { name: "5", exact: true }).click();
  await page.getByRole("button", { name: intake.next, exact: true }).click();

  await expect(page.getByRole("heading", { name: intake.reviewTitle })).toBeVisible();
  await page.getByRole("button", { name: intake.confirm }).click();
  await expect(page.getByRole("heading", { name: intake.doneTitle })).toBeVisible();
  await expect(page.getByText("MED-012")).toBeVisible();
  // A first visit gets an OPD card to bring next time.
  await expect(page.getByText("OPD number 26-000012 · PIN 482913")).toBeVisible();
  // The phone is already in the patient's hand: no QR to scan, no kiosk countdown.
  await expect(page.locator(".intake-ticket-qr")).toHaveCount(0);
  await expect(page.locator(".intake-restart")).toHaveCount(0);
  await expectNoHorizontalScroll(page);

  // Without randomUUID the app still sends a valid Idempotency-Key with every write.
  expect(api.idempotencyKeys.length).toBeGreaterThanOrEqual(3);
  for (const key of api.idempotencyKeys) expect(key).toMatch(UUID_V4);

  await page.getByRole("button", { name: kiosk.mobileViewStatus }).click();
  await expect(page).toHaveURL(new RegExp(`/s/${STATUS}$`));
  await expect(page.getByText("MED-012")).toBeVisible();

  // Scanning the poster again shows the same token instead of starting a second visit.
  await page.goto(`/m/${CODE}`);
  await expect(page.getByText(kiosk.mobileHaveToken.replace("{{token}}", "MED-012"))).toBeVisible();
  await expect(page.getByText("OPD number 26-000012 · PIN 482913")).toBeVisible();
  await expect(page.getByRole("link", { name: kiosk.mobileViewStatus })).toBeVisible();
  expect(api.sessionsStarted).toBe(1);
});

test("a refresh in the middle of the interview resumes the same visit", async ({ page }) => {
  const api = await installApiMocks(page);
  await page.goto(`/m/${CODE}`);
  await page.getByRole("button", { name: kiosk.mobileStart }).click();
  await page.getByRole("button", { name: intake.consentAgree }).click();
  await page.getByLabel(intake.ageQuestion).fill("29");
  await page.getByRole("radio", { name: intake.sexFemale }).click();
  await page.getByRole("button", { name: intake.next, exact: true }).click();
  await page.getByRole("button", { name: intake.returningNo }).click();
  await page.getByRole("button", { name: intake.dangerNone }).click();
  await page.getByRole("button", { name: "Fever" }).click();
  await page.getByRole("button", { name: intake.next, exact: true }).click();
  await expect(page.getByRole("heading", { name: "How bad is it?" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "How bad is it?" })).toBeVisible();

  // Reopening the link offers to continue the visit in progress rather than a new one.
  await page.goto(`/m/${CODE}`);
  await page.getByRole("button", { name: kiosk.mobileResume }).click();
  await expect(page.getByRole("heading", { name: "How bad is it?" })).toBeVisible();
  expect(api.sessionsStarted).toBe(1);
});

test("a returning patient joins today's visit to their record with their OPD card", async ({
  page,
}) => {
  await installApiMocks(page);
  const linked: string[] = [];
  await page.route("**/intake/sessions/*/opd-card", async (route) => {
    linked.push(route.request().postData() ?? "");
    await route.fulfill({
      json: {
        linked: true,
        opd_number: "26-000012",
        visit_count: 1,
        previous_visit: { id: "00000000-0000-7000-8000-00000000a000", date: "2026-10-03" },
      },
    });
  });
  let followUp: unknown;
  await page.route("**/intake/sessions/*/follow-up", async (route) => {
    followUp = JSON.parse(route.request().postData() ?? "{}");
    await route.fulfill({ json: { previous_visit_id: "00000000-0000-7000-8000-00000000a000" } });
  });

  await page.goto(`/m/${CODE}`);
  await page.getByRole("button", { name: kiosk.mobileStart }).click();
  await page.getByRole("button", { name: intake.consentAgree }).click();
  await page.getByLabel(intake.ageQuestion).fill("41");
  await page.getByRole("radio", { name: intake.sexMale, exact: true }).click();
  await page.getByRole("button", { name: intake.next, exact: true }).click();

  await expect(page.getByRole("heading", { name: intake.returningTitle })).toBeVisible();
  await page.getByRole("button", { name: intake.returningYes }).click();
  await page.getByLabel(intake.opdCardNumber).fill("26-000012");
  await page.getByLabel(intake.opdCardPin).fill("482913");
  await expectNoHorizontalScroll(page);
  await page.getByRole("button", { name: intake.next, exact: true }).click();

  await expect(page.getByRole("heading", { name: /same problem/ })).toBeVisible();
  await page.getByRole("button", { name: intake.followUpSame }).click();
  await expect(page.getByRole("button", { name: intake.dangerNone })).toBeVisible();
  expect(JSON.parse(linked[0])).toEqual({ number: "26-000012", pin: "482913" });
  expect(followUp).toEqual({ previous_visit_id: "00000000-0000-7000-8000-00000000a000" });
});

test("an invalid or paused check-in link says where to go instead", async ({ page }) => {
  await page.route("**/healthz", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route(`**/intake/checkin/${CODE}`, (route) =>
    route.fulfill({
      status: 404,
      json: { code: "CHECKIN_UNAVAILABLE", detail: "This check-in QR is not valid." },
    }),
  );
  await page.goto(`/m/${CODE}`);
  await expect(page.getByText(kiosk.mobileInvalid)).toBeVisible();

  await page.unroute(`**/intake/checkin/${CODE}`);
  await page.route(`**/intake/checkin/${CODE}`, (route) =>
    route.fulfill({
      status: 409,
      json: { code: "CHECKIN_TURNED_OFF", detail: "Phone check-in is paused." },
    }),
  );
  await page.reload();
  await expect(page.getByText(kiosk.mobilePaused)).toBeVisible();
});
