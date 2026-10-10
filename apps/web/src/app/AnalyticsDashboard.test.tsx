import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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

const summary = {
  patients: 12,
  average_wait_seconds: 240,
  average_consult_seconds: null,
  urgent_count: 0,
  language_mix: { hi: 8, en: 4 },
  average_interview_seconds: 90,
  confirmed_without_edits_percent: 75,
  doctor_edit_rate_percent: 25,
  department_load: [
    {
      department_id: "department-1",
      name: { en: "General Medicine", hi: "सामान्य चिकित्सा", mr: "सामान्य औषधोपचार" },
      patients: 12,
      urgent_count: 2,
      average_wait_seconds: 240,
    },
  ],
  start_day: "2026-10-05",
  end_day: "2026-10-07",
};

const settings = {
  minutes_saved_per_visit: 3,
  is_default: true,
  updated_at: null,
  updated_by_name: null,
};

function mockApi({ saveFails = false } = {}) {
  return vi.spyOn(staffAuth, "request").mockImplementation(async (path, init) => {
    if (path === "/analytics/settings") {
      if (init?.method === "PATCH") {
        if (saveFails) return new Response("{}", { status: 503 });
        const body = JSON.parse(String(init.body)) as { minutes_saved_per_visit: number };
        return new Response(
          JSON.stringify({
            ...body,
            is_default: false,
            updated_at: "2026-10-10T04:00:00Z",
            updated_by_name: "Demo Administrator",
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify(settings), { status: 200 });
    }
    if (path.startsWith("/admin/departments")) {
      return new Response(
        JSON.stringify({
          items: [{ id: "department-1", name_en: "General Medicine", name_hi: "", name_mr: "" }],
        }),
        { status: 200 },
      );
    }
    if (path.startsWith("/analytics/summary")) {
      return new Response(JSON.stringify(summary), { status: 200 });
    }
    if (path.startsWith("/analytics/export.csv")) {
      return new Response("service_date\n", { status: 200 });
    }
    return new Response(
      JSON.stringify({
        days: 30,
        items: [
          // Two departments on one day are one bar; the middle day has no row at all.
          { service_date: "2026-10-05", patient_count: 5 },
          { service_date: "2026-10-05", patient_count: 3 },
          { service_date: "2026-10-07", patient_count: 4 },
        ],
      }),
      { status: 200 },
    );
  });
}

function renderDashboard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <AnalyticsDashboard />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("Phase 11 analytics dashboard", () => {
  it("reports one period consistently and says 'No data' instead of a dash", async () => {
    const request = mockApi();
    renderDashboard();

    expect(await screen.findByText("General Medicine", { selector: "th" })).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith("/analytics/summary?days=30");
    expect(request).toHaveBeenCalledWith("/analytics/timeseries?days=30");
    const card = (label: string) => screen.getByText(label, { selector: "h3" }).closest("article")!;
    expect(card("Patient visits")).toHaveTextContent("12");
    expect(card("Average wait")).toHaveTextContent("4 min");
    expect(card("Average consultation")).toHaveTextContent("No data");
    expect(card("Average consultation")).toHaveTextContent("No consultation was finished");
    expect(card("Average consultation")).not.toHaveTextContent("—");
    // A real zero stays a zero.
    expect(card("Urgent cases")).toHaveTextContent("0");
    expect(card("Confirmed without edits")).toHaveTextContent("75%");

    // The chart covers every day of the period and adds up to the visit total.
    expect(
      screen.getByRole("img", { name: "Daily patient visits, Last 30 days: 12 in total" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/12 visits · Last 30 days/)).toBeInTheDocument();
    const languages = screen.getByText("Patient languages").closest("article")!;
    expect(languages).toHaveTextContent("66.7%");
  });

  it("changes the period and department for every figure and the export", async () => {
    const request = mockApi();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:csv");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    renderDashboard();
    const user = userEvent.setup();
    await screen.findByText("General Medicine", { selector: "th" });

    await user.selectOptions(screen.getByRole("combobox", { name: "Date range" }), "7");
    await user.selectOptions(screen.getByRole("combobox", { name: "Department" }), "department-1");
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith("/analytics/summary?days=7&department_id=department-1"),
    );
    expect(request).toHaveBeenCalledWith("/analytics/timeseries?days=7&department_id=department-1");

    await user.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith("/analytics/export.csv?days=7&department_id=department-1"),
    );
  });

  it("saves the time-saved estimate for the whole hospital, accepting only valid values", async () => {
    const request = mockApi();
    renderDashboard();
    const user = userEvent.setup();
    const timeSaved = () => screen.getByText("Estimated time saved", { selector: "h3" }).closest("article")!;
    await screen.findByText("General Medicine", { selector: "th" });
    await waitFor(() => expect(timeSaved()).toHaveTextContent("36 min"));
    expect(timeSaved()).toHaveTextContent("3 min × 12 visits");

    await user.click(screen.getByRole("button", { name: "Configure metric" }));
    const dialog = screen.getByRole("dialog", { name: "Estimated time saved" });
    expect(dialog).toHaveTextContent("every administrator sees the same estimate");
    const input = within(dialog).getByLabelText("Minutes saved per visit");
    await user.clear(input);
    await user.type(input, "90");
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(dialog).toHaveTextContent("Enter a number from 0 to 60.");

    await user.clear(input);
    await user.type(input, "4");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(request).toHaveBeenCalledWith(
      "/analytics/settings",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ minutes_saved_per_visit: 4 }) }),
    );
    expect(timeSaved()).toHaveTextContent("48 min");
    expect(window.localStorage.getItem("opd-analytics-baseline-minutes")).toBeNull();
  });

  it("keeps the dialog open and says so when saving fails", async () => {
    mockApi({ saveFails: true });
    renderDashboard();
    const user = userEvent.setup();
    await screen.findByText("General Medicine", { selector: "th" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Configure metric" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Configure metric" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("could not be saved");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
