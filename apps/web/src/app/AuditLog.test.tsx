import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { afterEach, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { AuditLog } from "./AuditLog";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const event = {
  id: "event-1",
  actor_type: "user",
  actor_id: "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
  actor_name: "Synthetic Doctor",
  action: "case_sheet.read",
  resource_type: "visit",
  resource_id: "0290f1e2-c3d4-7e5f-8a9b-0c1d2e3f4a5c",
  meta: null,
  created_at: "2026-10-22T10:15:00Z",
};

it("lists entries, searches by person and refuses a partial id", async () => {
  const user = userEvent.setup();
  const request = vi.spyOn(staffAuth, "request").mockImplementation(async (path) =>
    new Response(
      JSON.stringify({ items: String(path).includes("person=") ? [] : [event], next_cursor: null }),
      { status: 200 },
    ),
  );
  render(
    <I18nextProvider i18n={i18n}>
      <AuditLog />
    </I18nextProvider>,
  );
  expect(await screen.findByText("Synthetic Doctor")).toBeInTheDocument();
  expect(screen.getByText("case_sheet.read")).toBeInTheDocument();

  await user.type(screen.getByLabelText("Staff ID"), "0190a1b2");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText(/A record or staff ID is a full ID/)).toBeInTheDocument();

  // Clicking a person's short id fills the search with their full id.
  await user.clear(screen.getByLabelText("Staff ID"));
  await user.click(screen.getByRole("button", { name: "0190a1b2" }));
  await user.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() =>
    expect(request).toHaveBeenLastCalledWith(
      "/audit?person=0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
      { cache: "no-store" },
    ),
  );
  expect(await screen.findByText("No entries match.")).toBeInTheDocument();
});
