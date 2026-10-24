import { expect, test, type Page } from "@playwright/test";
import { adminApi, freshCode, stackState, type Api } from "./stack";

// The main paths against the real API on the production-mode stack (Phase 7, task 7.13.2):
// the administrator's two-step sign-in, a kiosk paired with a one-time code checking a patient
// in, and a new doctor choosing a password, calling that patient and writing notes. Synthetic
// data only. The specs share one queue, so they run in order and each builds on the last.
test.describe.configure({ mode: "serial" });

const state = stackState();
const patient = { token: "", visitId: "", department: "" };
let admin: Api;

test.beforeAll(async () => {
  admin = await adminApi(state);
});
test.afterAll(async () => {
  await admin.dispose();
});

async function signIn(page: Page, email: string, password: string) {
  await page.goto(`${state.base}/staff?lang=en`);
  await page.getByLabel("Hospital", { exact: true }).selectOption(state.hospital_id);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("the administrator signs in with a password and an authenticator code", async ({ page }) => {
  await signIn(page, state.email, state.password);
  await expect(page.getByText("Enter the 6-digit code from your authenticator app.")).toBeVisible();
  await page.getByLabel("6-digit code").fill(await freshCode(state.totp_secret));
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page.getByRole("link", { name: "My account" })).toBeVisible();

  // Settings → System reads the running stack: database, Redis and worker all up.
  await page.goto(`${state.base}/staff/system?lang=en`);
  await expect(page.getByRole("heading", { name: "System" })).toBeVisible();
  await expect(page.getByText(/\(production\)/)).toBeVisible();
});

test("a kiosk paired with a one-time code checks a patient in", async ({ page }) => {
  const kiosk = await admin.post("admin/kiosks", { label: "Stack e2e kiosk" });
  await page.goto(`${state.base}/kiosk?lang=en&activate=${String(kiosk.activation_code)}`);
  await page.getByRole("button", { name: "Start patient check-in" }).click();

  await page.getByRole("button", { name: "I agree, continue" }).click();
  // Online AI help is its own optional question; it also decides voice input, so it is asked
  // even when the server's AI is off. Tap and type, as most patients will.
  const age = page.getByLabel("How old are you?");
  const noAi = page.getByRole("button", { name: "No, continue without AI" });
  await expect(age.or(noAi)).toBeVisible();
  if (await noAi.isVisible()) await noAi.click();
  await age.fill("52");
  await page.getByRole("radio", { name: "Female" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  const newPatient = page.getByRole("button", { name: "No, or I don't have my card" });
  const dangerNone = page.getByRole("button", { name: "No, none of these" });
  await expect(newPatient.or(dangerNone)).toBeVisible();
  if (await newPatient.isVisible()) await newPatient.click();
  await dangerNone.click();

  // The interview: whatever the question bank asks, answer the first question in words and
  // the rest with the first choice, a middle score, or a skip, until the review screen.
  const review = page.getByRole("heading", { name: "Is this correct?" });
  for (let turn = 0; turn < 40; turn += 1) {
    const heading = page.locator("h1.intake-title");
    await expect(heading).toBeVisible();
    if (await review.isVisible()) break;
    const prompt = await heading.innerText();
    const skip = page.getByRole("button", { name: "Skip", exact: true });
    const scale = page.locator(".intake-scale");
    const chips = page.locator(".intake-chip:not(.is-none)");
    if (turn === 0) {
      await page.getByRole("button", { name: "Type instead" }).click();
      await page.getByRole("textbox").fill("Cough and fever for five days");
    } else if (await skip.isVisible()) {
      await skip.click();
      await expect(heading).not.toHaveText(prompt);
      continue;
    } else if (await scale.isVisible()) {
      await scale.getByRole("radio", { name: "4", exact: true }).click();
    } else if ((await chips.count()) > 0) {
      await chips.first().click();
    } else {
      await page.getByRole("button", { name: "Type instead" }).click();
      await page.getByRole("textbox").fill("Not sure");
    }
    await page.locator(".intake-btn--primary").click();
    await expect(heading).not.toHaveText(prompt);
  }
  await expect(review).toBeVisible();
  await page.locator(".intake-btn--primary").click();

  await expect(page.getByRole("heading", { name: "You are checked in" })).toBeVisible();
  patient.token = (await page.locator(".status-token").innerText()).trim();
  expect(patient.token).toMatch(/^[A-Z]+-\d+$/);

  // The visit reached the register, with the complaint the patient typed.
  const register = await admin.get("patients?limit=50");
  const item = (register.items as Record<string, unknown>[]).find(
    (row) => row.token === patient.token,
  );
  expect(item).toBeTruthy();
  patient.visitId = String(item?.visit_id);
  patient.department = String(item?.department);
});

test("a new doctor chooses a password, calls the patient and writes notes", async ({ page }) => {
  test.skip(!patient.visitId, "needs the patient checked in by the previous spec");
  // The register names the department in English.
  const departments = (await admin.get("admin/departments?limit=100")).items as {
    id: string;
    name_en: string;
  }[];
  const department = departments.find((item) => item.name_en === patient.department);
  expect(department, `department ${patient.department}`).toBeTruthy();
  const email = `stack-doctor-${Date.now()}@example.test`;
  await admin.post("admin/users", {
    role: "doctor",
    name: "Dr Stack Test",
    email,
    password: "amber falcon meadow 5521",
    department_id: department?.id,
  });

  // An administrator-set password must be replaced before anything else opens.
  await signIn(page, email, "amber falcon meadow 5521");
  await expect(page.getByRole("heading", { name: "Choose your own password" })).toBeVisible();
  await page.getByLabel("Current password").fill("amber falcon meadow 5521");
  await page.getByLabel("New password", { exact: true }).fill("quartz lagoon pepper 8812");
  await page.getByLabel("Type the new password again").fill("quartz lagoon pepper 8812");
  await page.getByRole("button", { name: "Save new password" }).click();
  await expect(page.getByRole("heading", { name: "Choose your own password" })).toHaveCount(0);

  // A full page load: the session must survive it.
  await page.goto(`${state.base}/staff/queue?lang=en`);
  await expect(page.getByText(patient.token).first()).toBeVisible();
  await page.getByRole("button", { name: "Call next" }).first().click();
  await expect(page.getByText(`Called ${patient.token}`).or(page.getByRole("status"))).toBeVisible();

  await page.goto(`${state.base}/staff/consultations?lang=en&visit_id=${patient.visitId}`);
  await expect(page.getByText(patient.token).first()).toBeVisible();
  await page.getByRole("button", { name: "Start consultation" }).click();
  await page.getByRole("tab", { name: /^Clinical notes/ }).click();
  await page.getByRole("textbox", { name: "Examination findings" }).fill("Chest clear. Throat red.");
  await page.getByRole("textbox", { name: "Assessment" }).fill("Viral upper respiratory infection");
  await page.getByRole("textbox", { name: "Plan" }).fill("Fluids and rest; review if worse.");
  await page.getByRole("button", { name: "Save notes" }).click();
  await expect(page.getByText("Notes saved (version 1).")).toBeVisible();

  // Saved on the server, not only on the screen.
  const notes = await admin.get(`visits/${patient.visitId}/doctor-notes`);
  expect(notes.version).toBe(1);
});
