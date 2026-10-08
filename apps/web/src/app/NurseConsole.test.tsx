import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { NurseConsole } from "./NurseConsole";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("Phase 7.5 nurse assisted registration", () => {
  it("only opens case sheets after a patient has submitted their history", async () => {
    vi.spyOn(staffAuth, "user", "get").mockReturnValue({
      id: "doctor-1",
      name: "Synthetic Doctor",
      email: "doctor@example.test",
      role: "doctor",
      hospital_id: "hospital-1",
    });
    vi.spyOn(staffAuth, "request").mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              visit_id: "submitted-visit",
              token: "MED-001",
              status: "queued",
              urgency: "routine",
              age_years: 40,
              sex: "unknown",
              department: "General Medicine",
              registered_at: "2026-10-07T12:00:00Z",
              complaint: "Headache",
              case_sheet_available: true,
            },
            {
              visit_id: "unfinished-visit",
              token: null,
              status: "registering",
              urgency: "routine",
              age_years: 40,
              sex: "unknown",
              department: "General Medicine",
              registered_at: "2026-10-07T12:01:00Z",
              complaint: "",
              case_sheet_available: false,
            },
          ],
        }),
        { status: 200 },
      ),
    );

    render(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <MemoryRouter>
            <NurseConsole />
          </MemoryRouter>
        </I18nextProvider>
      </QueryClientProvider>,
    );

    const link = await screen.findByRole("link", { name: "Open case sheet" });
    expect(link).toHaveAttribute(
      "href",
      "/staff/consultations?visit_id=submitted-visit&status=queued",
    );
    expect(screen.queryByText("History not submitted")).not.toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: /Check-in not finished/ }));
    expect(screen.getByText("History not submitted")).toBeInTheDocument();
    expect(
      screen.queryAllByRole("link", { name: "Open case sheet" }),
    ).toHaveLength(0);
  });

  it("starts an assigned nurse session and presents the consent copy first", async () => {
    vi.spyOn(staffAuth, "user", "get").mockReturnValue({
      id: "nurse-1",
      name: "Synthetic Nurse",
      email: "nurse@example.test",
      role: "nurse",
      hospital_id: "hospital-1",
    });
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (path) => {
        if (path.startsWith("/patients")) {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        return new Response(
          JSON.stringify({
            visit_id: "visit-1",
            patient_access_token: "synthetic-patient-token",
            expires_in: 3600,
            consent: {
              language: "en",
              version: "synthetic-version",
              sha256: "0".repeat(64),
              text: "Synthetic consent copy for testing.",
            },
            assisted: true,
          }),
          { status: 201 },
        );
      });
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ granted: true, next: "identity" }), {
        status: 200,
      }),
    );

    render(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <MemoryRouter>
            <NurseConsole />
          </MemoryRouter>
        </I18nextProvider>
      </QueryClientProvider>,
    );

    await userEvent
      .setup()
      .click(
        screen.getByRole("button", { name: "Review consent with patient" }),
      );
    expect(
      await screen.findByText("Synthetic consent copy for testing."),
    ).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith(
      "/intake/assisted-sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ language: "en" }),
      }),
    );
    await userEvent
      .setup()
      .click(
        screen.getByRole("button", { name: "I agree, continue" }),
      );
    expect(await screen.findByLabelText("Patient's full name")).toBeInTheDocument();
  });
});
