import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import i18n from "../i18n";
import { useLanguageStore } from "../languageStore";
import { KioskShell } from "./KioskShell";
import { KioskWelcome } from "./KioskWelcome";
import { KioskPreview } from "./KioskPreview";
import { PatientStatus } from "./PatientStatus";
import { StaffShell } from "./StaffShell";

function renderRoute(initialPath: string, routes: React.ReactNode) {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={[initialPath]}>{routes}</MemoryRouter>
    </I18nextProvider>,
  );
}

afterEach(() => {
  cleanup();
  useLanguageStore.getState().setLanguage("en");
  vi.useRealTimers();
});

describe("Phase 1 preview screens", () => {
  it("shows S1 welcome, language choices, and the preview boundary", () => {
    renderRoute("/kiosk", <KioskWelcome />);
    expect(
      screen.getByRole("heading", { name: "Welcome" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Choose your language" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/does not save or submit information/i),
    ).toBeInTheDocument();
  });

  it("shows selectable generic tiles on the static S3 screen", async () => {
    const user = userEvent.setup();
    renderRoute(
      "/kiosk/preview/s3",
      <Routes>
        <Route path="/kiosk/preview/:step" element={<KioskPreview />} />
      </Routes>,
    );
    const concern = screen.getByRole("button", { name: "Example concern one" });
    await user.click(concern);
    expect(concern).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getAllByRole("button", { name: /example concern/i }),
    ).toHaveLength(8);
  });

  it("provides a demo-only staff login and safe submit message", async () => {
    const user = userEvent.setup();
    renderRoute("/staff", <StaffShell />);
    expect(
      screen.getByText(/do not enter a real password/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByText(/no account was checked/i)).toBeInTheDocument();
  });

  it("shows only the fictional status token on /s/demo", () => {
    renderRoute(
      "/s/demo",
      <Routes>
        <Route path="/s/:token" element={<PatientStatus />} />
      </Routes>,
    );
    expect(screen.getByText("DEMO-024")).toBeInTheDocument();
    expect(screen.getByText(/demo preview/i)).toBeInTheDocument();
  });

  it("warns after 60 seconds and clears session language after the 15-second grace period", async () => {
    vi.useFakeTimers();
    useLanguageStore.getState().setLanguage("mr");
    renderRoute(
      "/kiosk",
      <Routes>
        <Route path="/kiosk" element={<KioskShell />}>
          <Route index element={<KioskWelcome />} />
        </Route>
      </Routes>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(useLanguageStore.getState().language).toBe("en");
  });
});
