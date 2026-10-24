import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { PrivacySettingsCard } from "./PrivacySettingsCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const settings = (contact: string, url: string | null = "https://opd.example.test/privacy/h1") =>
  new Response(
    JSON.stringify({
      grievance_contact: contact,
      notice_path: "/privacy/h1",
      notice_url: url,
      updated_at: null,
      updated_by_name: null,
    }),
    { status: 200 },
  );

function renderCard() {
  render(
    <I18nextProvider i18n={i18n}>
      <PrivacySettingsCard />
    </I18nextProvider>,
  );
}

describe("privacy notice settings", () => {
  it("saves the grievance officer's contact", async () => {
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (_path, init) =>
        init?.method === "PUT" ? settings("Dr S. Rao, Room 12") : settings(""),
      );
    renderCard();
    const contact = await screen.findByRole("textbox", {
      name: "Grievance officer: name and how to reach them",
    });
    await waitFor(() => expect(contact).toBeEnabled());
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(contact, { target: { value: "Dr S. Rao, Room 12" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved. The notice shows it at once.")).toHaveAttribute(
      "role",
      "status",
    );
    expect(request).toHaveBeenCalledWith(
      "/admin/privacy-settings",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ grievance_contact: "Dr S. Rao, Room 12" }),
      }),
    );
    expect(screen.getByRole("link", { name: "Open the notice" })).toHaveAttribute(
      "href",
      "/privacy/h1",
    );
  });

  it("shows the poster in all three languages with the notice's QR", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async () => settings(""));
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Poster with QR" }));
    expect(screen.getByText("How we use your information")).toBeVisible();
    expect(screen.getByText("हम आपकी जानकारी का उपयोग कैसे करते हैं")).toBeVisible();
    expect(screen.getByText("आम्ही तुमची माहिती कशी वापरतो")).toBeVisible();
    expect(screen.getByText("https://opd.example.test/privacy/h1")).toBeVisible();
    expect(screen.getByRole("button", { name: "Print the poster" })).toBeVisible();
  });

  it("says why there is no QR when the server does not know its address", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async () => settings("", null));
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Poster with QR" }));
    expect(screen.getByRole("alert")).toHaveTextContent("PUBLIC_WEB_URL");
  });
});
