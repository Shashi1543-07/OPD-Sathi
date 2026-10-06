import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { AnalyticsDashboard } from "./AnalyticsDashboard";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("Phase 11 analytics dashboard", () => {
  it("shows tenant summary, department activity, and editable time baseline", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path === "/analytics/summary") {
        return new Response(JSON.stringify({
          patients: 12,
          average_wait_seconds: 240,
          average_consult_seconds: 360,
          urgent_count: 2,
          language_mix: { hi: 8, en: 4 },
          average_interview_seconds: 90,
          confirmed_without_edits_percent: 75,
          doctor_edit_rate_percent: 25,
          department_load: [{
            department_id: "department-1",
            name: { en: "General Medicine", hi: "सामान्य चिकित्सा", mr: "सामान्य औषधोपचार" },
            patients: 12,
            urgent_count: 2,
            average_wait_seconds: 240,
          }],
        }), { status: 200 });
      }
      return new Response(JSON.stringify({ days: 30, items: [
        { service_date: "2026-10-07", patient_count: 12 },
      ] }), { status: 200 });
    });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><I18nextProvider i18n={i18n}><AnalyticsDashboard /></I18nextProvider></QueryClientProvider>);
    expect(await screen.findByText("General Medicine")).toBeInTheDocument();
    expect(screen.getByText("Confirmed without edits").closest("article")).toHaveTextContent("75");
    expect(screen.getByRole("img", { name: "Daily patient counts for the last 30 days" })).toBeInTheDocument();

    const baseline = screen.getByLabelText("Editable minutes saved per patient");
    const user = userEvent.setup();
    await user.clear(baseline);
    await user.type(baseline, "4");
    const timeSavedCard = screen.getByText("Estimated time saved").closest("article");
    expect(timeSavedCard).toHaveTextContent("48");
    expect(timeSavedCard).toHaveTextContent("min");
    expect(window.localStorage.getItem("opd-analytics-baseline-minutes")).toBe("4");
  });
});
