import { cleanup, render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { SystemStatus } from "./SystemStatus";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const status = {
  version: "0.7.0",
  environment: "production",
  checked_at: "2026-10-23T08:00:00Z",
  database: "up",
  redis: "up",
  worker: "stale",
  backup: { last_success: "2026-10-21T20:00:00Z", age_hours: 36 },
  disk_free_ratio: 0.15,
  certificate: { expires_at: "2027-01-01T00:00:00Z", days_left: 70 },
  ai: { enabled: false, llm: "mock", speech_to_text: "browser", text_to_speech: "browser", sms: "console" },
  kiosks: [
    { id: "k1", label: "Main hall kiosk", state: "online", last_seen_at: "2026-10-23T07:59:00Z" },
    { id: "k2", label: "ENT kiosk", state: "silent", last_seen_at: "2026-10-23T06:00:00Z" },
  ],
};

function pill(label: string) {
  const row = screen.getByText(label).closest(".sys-row");
  expect(row).not.toBeNull();
  return within(row as HTMLElement).getByText(/./, { selector: ".patient-status-pill" });
}

it("shows each part of the installation with the alerts' thresholds", async () => {
  vi.spyOn(staffAuth, "request").mockResolvedValue(new Response(JSON.stringify(status), { status: 200 }));
  render(
    <I18nextProvider i18n={i18n}>
      <SystemStatus />
    </I18nextProvider>,
  );
  expect(await screen.findByText("0.7.0 (production)")).toBeInTheDocument();
  expect(pill("Database")).toHaveClass("patient-status-pill--done");
  // A worker that stopped responding, a backup older than 26 hours: red, as the alerts.
  expect(pill("Background worker")).toHaveTextContent("Stopped responding");
  expect(pill("Background worker")).toHaveClass("patient-status-pill--cancelled");
  expect(pill("Last backup")).toHaveClass("patient-status-pill--cancelled");
  // 15% free disk: the 80%-full warning.
  expect(pill("Data disk")).toHaveTextContent("15% free");
  expect(pill("Data disk")).toHaveClass("patient-status-pill--queued");
  expect(pill("AI help")).toHaveTextContent("Off");
  const kiosks = screen.getByRole("region", { name: "Kiosks" });
  expect(within(kiosks).getByText("ENT kiosk")).toBeInTheDocument();
  expect(within(kiosks).getByText("Silent")).toHaveClass("patient-status-pill--cancelled");
});

it("says when the status cannot be loaded", async () => {
  vi.spyOn(staffAuth, "request").mockResolvedValue(new Response("{}", { status: 503 }));
  render(
    <I18nextProvider i18n={i18n}>
      <SystemStatus />
    </I18nextProvider>,
  );
  expect(await screen.findByText(/system status could not be loaded/)).toBeInTheDocument();
});
