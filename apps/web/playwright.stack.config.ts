import { defineConfig, devices } from "@playwright/test";

// End-to-end against the real API (Phase 7, task 7.13.2): no mocks. The production-mode stack
// must already be running (scripts/prod_smoke.sh with KEEP=1 and SMOKE_STATE_FILE); STACK_STATE
// names that state file. The specs share one hospital and one queue, so they run in order.
export default defineConfig({
  testDir: "./e2e-stack",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  use: {
    // The smoke stack's certificate is self-signed; hospital installs use a real one.
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
});
