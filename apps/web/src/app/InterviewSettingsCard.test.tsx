import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { InterviewSettingsCard } from "./InterviewSettingsCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const settings = (ayurveda: boolean, reviewOfSystems = false) =>
  new Response(
    JSON.stringify({
      ayurveda_module: ayurveda,
      review_of_systems_module: reviewOfSystems,
      updated_at: null,
      updated_by_name: null,
    }),
    { status: 200 },
  );

describe("interview settings", () => {
  it("turns the Ayurveda history on for the hospital", async () => {
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (_path, init) =>
        init?.method === "PATCH" ? settings(true) : settings(false),
      );
    render(
      <I18nextProvider i18n={i18n}>
        <InterviewSettingsCard />
      </I18nextProvider>,
    );

    const toggle = await screen.findByRole("checkbox", {
      name: "Ask the Ayurveda history questions",
    });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      await screen.findByText("Saved. Applies to interviews that start from now."),
    ).toHaveAttribute("role", "status");
    expect(request).toHaveBeenCalledWith(
      "/admin/interview-settings",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ ayurveda_module: true }),
      }),
    );
  });

  it("turns on the review of systems alone, sending only that switch", async () => {
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (_path, init) =>
        init?.method === "PATCH" ? settings(false, true) : settings(false),
      );
    render(
      <I18nextProvider i18n={i18n}>
        <InterviewSettingsCard />
      </I18nextProvider>,
    );

    const toggle = await screen.findByRole("checkbox", { name: "Ask the review of systems" });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).toHaveAccessibleDescription(/one choice per body system/);
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved. Applies to interviews that start from now.");
    expect(request).toHaveBeenCalledWith(
      "/admin/interview-settings",
      expect.objectContaining({ body: JSON.stringify({ review_of_systems_module: true }) }),
    );
    expect(screen.getByRole("checkbox", { name: "Ask the review of systems" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Ask the Ayurveda history questions" })).not.toBeChecked();
  });
});
