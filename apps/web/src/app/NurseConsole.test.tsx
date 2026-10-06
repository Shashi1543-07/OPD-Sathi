import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  it("starts an assigned nurse session and presents the consent copy first", async () => {
    vi.spyOn(staffAuth, "user", "get").mockReturnValue({
      id: "nurse-1",
      name: "Synthetic Nurse",
      email: "nurse@example.test",
      role: "nurse",
      hospital_id: "hospital-1",
    });
    const request = vi.spyOn(staffAuth, "request").mockResolvedValue(
      new Response(
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
      ),
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ granted: true, next: "identity" }), {
        status: 200,
      }),
    );

    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <NurseConsole />
        </MemoryRouter>
      </I18nextProvider>,
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
        screen.getByRole("button", { name: "I agree and want to continue" }),
      );
    expect(await screen.findByLabelText("Patient name")).toBeRequired();
  });
});
