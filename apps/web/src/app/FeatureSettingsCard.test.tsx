import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { FeatureSettingsCard } from "./FeatureSettingsCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const settings = (sms: boolean, phone = true, provider = true) =>
  new Response(
    JSON.stringify({
      sms_messages: sms,
      phone_checkin: phone,
      sms_provider: provider,
      updated_at: null,
      updated_by_name: null,
    }),
    { status: 200 },
  );

function renderCard() {
  render(
    <I18nextProvider i18n={i18n}>
      <FeatureSettingsCard />
    </I18nextProvider>,
  );
}

describe("feature settings", () => {
  it("shows SMS off and phone check-in on, and turns SMS on alone", async () => {
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (_path, init) =>
        init?.method === "PATCH" ? settings(true) : settings(false),
      );
    renderCard();

    const sms = await screen.findByRole("checkbox", { name: "Offer queue messages by SMS" });
    await waitFor(() => expect(sms).toBeEnabled());
    expect(sms).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Allow check-in on patients' own phones" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(sms);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved. Applies to check-ins that start from now.")).toHaveAttribute(
      "role",
      "status",
    );
    expect(request).toHaveBeenCalledWith(
      "/admin/feature-settings",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ sms_messages: true }) }),
    );
  });

  it("says when the server has no SMS provider", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async () => settings(false, true, false));
    renderCard();
    const sms = await screen.findByRole("checkbox", { name: "Offer queue messages by SMS" });
    await waitFor(() => expect(sms).toBeEnabled());
    expect(sms).toHaveAccessibleDescription(/no SMS provider yet/);
  });

  it("turns phone check-in off and reports a failed save", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async (_path, init) =>
      init?.method === "PATCH" ? new Response("{}", { status: 500 }) : settings(false),
    );
    renderCard();
    const phone = await screen.findByRole("checkbox", { name: "Allow check-in on patients' own phones" });
    await waitFor(() => expect(phone).toBeEnabled());
    fireEvent.click(phone);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save. Try again.");
  });
});
