import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

function signedInAdmin() {
  vi.spyOn(staffAuth, "user", "get").mockReturnValue({
    id: "admin-1",
    name: "Synthetic Administrator",
    email: "admin@example.test",
    role: "admin",
    hospital_id: "hospital-1",
  });
}

const department = {
  id: "dept-1",
  code: "GENMED",
  token_prefix: "MED",
  name_en: "General Medicine",
  name_hi: "सामान्य चिकित्सा",
  name_mr: "सामान्य औषधोपचार",
  is_active: true,
};
const person = (id: string, name: string, role: string, extra: Record<string, unknown> = {}) => ({
  id,
  name,
  role,
  email: `${id}@example.test`,
  department_id: "dept-1",
  is_active: true,
  ...extra,
});

function renderConsole() {
  return render(
    <I18nextProvider i18n={i18n}>
      <AdminConsole />
    </I18nextProvider>,
  );
}

describe("Phase 7.6 administrator console", () => {
  it("shows a newly created kiosk activation code once in the current screen", async () => {
    signedInAdmin();
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (path, init) => {
        if (path === "/admin/kiosks" && init?.method === "POST") {
          return json(
            {
              id: "kiosk-1",
              label: "Synthetic reception",
              activation_code: "one-time-synthetic-code",
            },
            201,
          );
        }
        return json({ items: [], next_cursor: null });
      });

    renderConsole();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Kiosks" }));
    await user.type(screen.getByLabelText(/Kiosk label/), "Synthetic reception");
    await user.click(screen.getByRole("button", { name: "Register kiosk" }));

    expect(await screen.findByText("one-time-synthetic-code")).toBeInTheDocument();
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
      expect(screen.queryByText("one-time-synthetic-code")).not.toBeInTheDocument(),
    );
  });

  it("reads every page of staff, then searches and filters them", async () => {
    signedInAdmin();
    const request = vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/admin/departments")) return json({ items: [department], next_cursor: null });
      if (path === "/admin/users?limit=100") {
        return json({
          items: [
            person("admin-1", "Synthetic Administrator", "admin"),
            person("doc-1", "Demo Doctor 01", "doctor"),
          ],
          next_cursor: "page-2",
        });
      }
      if (path === "/admin/users?limit=100&cursor=page-2") {
        return json({
          items: [
            person("nurse-1", "Demo Nurse 01", "nurse"),
            person("doc-2", "Demo Doctor 02", "doctor", { is_active: false }),
          ],
          next_cursor: null,
        });
      }
      return json({ items: [], next_cursor: null });
    });

    renderConsole();
    const list = await screen.findByRole("region", { name: "Staff accounts list" });
    expect(await within(list).findByText("Demo Nurse 01")).toBeVisible();
    expect(request).toHaveBeenCalledWith("/admin/users?limit=100&cursor=page-2", {});
    expect(screen.getByText("4 records")).toBeVisible();
    expect(within(list).getAllByText("General Medicine")).toHaveLength(4);

    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by role" }), "doctor");
    expect(within(list).getAllByRole("row")).toHaveLength(3);
    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by status" }), "inactive");
    expect(within(list).getAllByRole("row")).toHaveLength(2);
    expect(within(list).getByText("Demo Doctor 02")).toBeVisible();
    expect(screen.getByText("Showing 1 of 4")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await user.type(screen.getByRole("searchbox", { name: "Search by name or email" }), "nurse-1@");
    expect(within(list).getAllByRole("row")).toHaveLength(2);
    expect(within(list).getByText("Demo Nurse 01")).toBeVisible();
  });

  it("asks before deactivating, and never lets admins deactivate themselves", async () => {
    signedInAdmin();
    const request = vi.spyOn(staffAuth, "request").mockImplementation(async (path, init) => {
      if (init?.method === "PATCH") return json({ id: "doc-1", is_active: false });
      if (path.startsWith("/admin/departments")) return json({ items: [department], next_cursor: null });
      if (path.startsWith("/admin/users")) {
        return json({
          items: [
            person("admin-1", "Synthetic Administrator", "admin"),
            person("doc-1", "Demo Doctor 01", "doctor"),
          ],
          next_cursor: null,
        });
      }
      return json({ items: [], next_cursor: null });
    });

    renderConsole();
    expect(
      await screen.findByRole("button", { name: "Deactivate Synthetic Administrator" }),
    ).toBeDisabled();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Deactivate Demo Doctor 01" }));
    const dialog = screen.getByRole("alertdialog", { name: "Deactivate Demo Doctor 01?" });
    expect(dialog).toHaveTextContent("can't sign in again");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(request).not.toHaveBeenCalledWith("/admin/users/doc-1", expect.anything());

    await user.click(screen.getByRole("button", { name: "Deactivate Demo Doctor 01" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Deactivate" }),
    );
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/admin/users/doc-1",
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ is_active: false }) }),
      ),
    );
  });
});
