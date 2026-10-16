import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { QueueSettingsCard } from "./QueueSettingsCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const settings = (seconds: number, isDefault: boolean) =>
  new Response(
    JSON.stringify({
      call_grace_seconds: seconds,
      is_default: isDefault,
      updated_at: null,
      updated_by_name: null,
    }),
    { status: 200 },
  );

describe("call grace period setting", () => {
  it("shows the period in minutes and saves it in seconds", async () => {
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (_path, init) =>
        init?.method === "PATCH" ? settings(90, false) : settings(180, true),
      );
    render(
      <I18nextProvider i18n={i18n}>
        <QueueSettingsCard />
      </I18nextProvider>,
    );

    const field = await screen.findByRole("spinbutton", {
      name: /Wait before a patient can be marked absent/,
    });
    await waitFor(() => expect(field).toHaveValue(3));
    expect(screen.getByText("Default")).toBeInTheDocument();

    fireEvent.change(field, { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved. Applies to the next call.")).toHaveAttribute(
      "role",
      "status",
    );
    expect(request).toHaveBeenCalledWith(
      "/admin/queue-settings",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ call_grace_seconds: 90 }),
      }),
    );
  });
});
