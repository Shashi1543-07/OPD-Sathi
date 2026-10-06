import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { AdminConsole } from "./AdminConsole";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Phase 7.6 administrator console", () => {
  it("shows a newly created kiosk activation code once in the current screen", async () => {
    vi.spyOn(staffAuth, "user", "get").mockReturnValue({
      id: "admin-1",
      name: "Synthetic Administrator",
      email: "admin@example.test",
      role: "admin",
      hospital_id: "hospital-1",
    });
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (path, init) => {
        if (path.startsWith("/admin/departments")) {
          return new Response(
            JSON.stringify({ items: [], next_cursor: null }),
            {
              status: 200,
            },
          );
        }
        if (path === "/admin/users?limit=100") {
          return new Response(
            JSON.stringify({ items: [], next_cursor: null }),
            {
              status: 200,
            },
          );
        }
        if (path === "/admin/kiosks" && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              id: "kiosk-1",
              label: "Synthetic reception",
              activation_code: "one-time-synthetic-code",
            }),
            { status: 201 },
          );
        }
        return new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
        });
      });

    render(
      <I18nextProvider i18n={i18n}>
        <AdminConsole />
      </I18nextProvider>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Kiosks" }));
    await user.type(
      screen.getByLabelText("Kiosk label"),
      "Synthetic reception",
    );
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText("one-time-synthetic-code"),
    ).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith(
      "/admin/kiosks",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Idempotency-Key": expect.any(String),
        }),
      }),
    );
    await user.click(screen.getByRole("button", { name: "I copied the code" }));
    await waitFor(() =>
      expect(
        screen.queryByText("one-time-synthetic-code"),
      ).not.toBeInTheDocument(),
    );
  });
});
