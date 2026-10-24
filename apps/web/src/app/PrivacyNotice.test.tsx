import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { useLanguageStore } from "../languageStore";
import { PrivacyNotice } from "./PrivacyNotice";

const HOSPITAL = "01a16c12-e285-799d-9138-e0537045382c";

function notice(language: string, contact: string | null, status = "draft_needs_clinician_review") {
  return new Response(
    JSON.stringify({
      hospital_name: "Synthetic District Hospital",
      grievance_contact: contact,
      notice: {
        language,
        version: "privacy-notice-1.0",
        review_status: status,
        text: `## Who is responsible (${language})\n\nThis hospital.\n\n- Your age\n- Your answers`,
      },
    }),
    { status: 200 },
  );
}

function renderNotice() {
  render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={[`/privacy/${HOSPITAL}`]}>
        <Routes>
          <Route path="/privacy/:hospitalId" element={<PrivacyNotice />} />
        </Routes>
      </MemoryRouter>
    </I18nextProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useLanguageStore.getState().setLanguage("en");
});

describe("privacy notice", () => {
  it("shows the hospital's notice, marks a draft, and changes language", async () => {
    const fetched: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      fetched.push(url);
      return notice(url.endsWith("language=hi") ? "hi" : "en", null);
    });
    renderNotice();

    expect(await screen.findByRole("heading", { name: "Who is responsible (en)" })).toBeVisible();
    expect(screen.getByText("Synthetic District Hospital")).toBeVisible();
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Your age",
      "Your answers",
    ]);
    expect(screen.getByRole("note")).toHaveTextContent("Draft");
    expect(
      screen.getByText("Ask at the registration desk how to reach the grievance officer."),
    ).toBeVisible();
    expect(fetched[0]).toContain(`/privacy-notice/${HOSPITAL}?language=en`);

    await userEvent.click(screen.getByRole("button", { name: "हिन्दी" }));
    expect(await screen.findByRole("heading", { name: "Who is responsible (hi)" })).toBeVisible();
    await waitFor(() => expect(fetched.at(-1)).toContain("language=hi"));
  });

  it("shows the grievance officer and no draft mark once approved", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      notice("en", "Dr S. Rao, Room 12", "approved"),
    );
    renderNotice();
    expect(await screen.findByText("Dr S. Rao, Room 12")).toBeVisible();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("says when the notice cannot be loaded", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 404 }));
    renderNotice();
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
  });
});
