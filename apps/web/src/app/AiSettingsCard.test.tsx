import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { AiSettingsCard } from "./AiSettingsCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const settings = (body: { llm_enabled: boolean; chosen: boolean; available: boolean }) =>
  new Response(JSON.stringify(body), { status: 200 });

function renderCard() {
  render(
    <I18nextProvider i18n={i18n}>
      <AiSettingsCard />
    </I18nextProvider>,
  );
}

describe("AI settings", () => {
  it("switches online AI help off for the hospital", async () => {
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (_path, init) =>
        init?.method === "PATCH"
          ? settings({ llm_enabled: false, chosen: true, available: true })
          : settings({ llm_enabled: true, chosen: true, available: true }),
      );
    renderCard();

    const toggle = await screen.findByRole("checkbox", {
      name: "Allow online AI help in this hospital",
    });
    await waitFor(() => expect(toggle).toBeChecked());
    expect(toggle).toHaveAccessibleDescription(/Each patient still chooses/);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved. Applies from now.")).toHaveAttribute("role", "status");
    expect(request).toHaveBeenCalledWith(
      "/admin/ai-settings",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ llm_enabled: false }) }),
    );
    expect(toggle).not.toBeChecked();
  });

  it("says when no choice is saved yet, and lets the admin save the default", async () => {
    vi.spyOn(staffAuth, "request").mockResolvedValue(
      settings({ llm_enabled: true, chosen: false, available: true }),
    );
    renderCard();

    const toggle = await screen.findByRole("checkbox", {
      name: "Allow online AI help in this hospital",
    });
    await waitFor(() => expect(toggle).toHaveAccessibleDescription(/No choice saved yet/));
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("says the switch has no effect when the server has no AI", async () => {
    vi.spyOn(staffAuth, "request").mockResolvedValue(
      settings({ llm_enabled: true, chosen: true, available: false }),
    );
    renderCard();

    const toggle = await screen.findByRole("checkbox", {
      name: "Allow online AI help in this hospital",
    });
    await waitFor(() => expect(toggle).toHaveAccessibleDescription(/not set up on this server/));
  });
});
