import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: ["**/phase-10-offline.spec.ts"],
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173/kiosk",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // Specs mock every API call they rely on. Point the /api proxy at a closed port so a
    // request a spec forgot to mock fails fast, as in CI, instead of reaching a local API.
    env: { VITE_DEV_API_TARGET: "http://127.0.0.1:9" },
  },
});
