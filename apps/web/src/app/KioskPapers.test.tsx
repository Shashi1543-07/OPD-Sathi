import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { intake, IntakeRequestError } from "../lib/intake";
import { KioskPapers } from "./KioskPapers";

beforeEach(async () => {
  await i18n.changeLanguage("en");
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:synthetic-paper"),
  });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const photo = () =>
  new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], "paper.jpg", { type: "image/jpeg" });

function renderPapers(online = false) {
  return render(
    <I18nextProvider i18n={i18n}>
      <KioskPapers online={online} disabled={false} />
    </I18nextProvider>,
  );
}

describe("Old papers on the kiosk review screen", () => {
  it("keeps a photo of the paper the patient picked, and takes it back on request", async () => {
    vi.spyOn(intake, "documents").mockResolvedValue({ documents: [], limit: 5 });
    const add = vi.spyOn(intake, "addDocument").mockResolvedValue({
      id: "paper-1",
      kind: "lab_report",
      size: 4,
      added_at: "2026-10-17T04:30:00Z",
    });
    const remove = vi.spyOn(intake, "removeDocument").mockResolvedValue(undefined);
    renderPapers();
    const user = userEvent.setup();

    expect(screen.getByRole("heading", { name: "Old papers (optional)" })).toBeVisible();
    // Without AI help the doctor simply looks at the photos.
    expect(screen.getByText(/The doctor will look at the photos\./)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Test report" }));
    await user.upload(screen.getByTestId("papers-file"), photo());

    expect(await screen.findByRole("img", { name: "Photo 1: Test report" })).toHaveAttribute(
      "src",
      "blob:synthetic-paper",
    );
    expect(add).toHaveBeenCalledWith(expect.any(File), "lab_report");
    expect(screen.getByText("Photo saved for the doctor.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Remove photo 1: Test report" }));
    await waitFor(() => expect(screen.queryByRole("img")).toBeNull());
    expect(remove).toHaveBeenCalledWith("paper-1");
    expect(screen.getByText("Photo removed.")).toBeVisible();
  });

  it("says that AI reads the papers only when the patient allowed AI help", () => {
    vi.spyOn(intake, "documents").mockResolvedValue({ documents: [], limit: 5 });
    renderPapers(true);
    expect(
      screen.getByText(/AI will read them for the doctor, who checks each one against the photo\./),
    ).toBeVisible();
  });

  it("lists photos added before a reload and stops at the limit", async () => {
    vi.spyOn(intake, "documents").mockResolvedValue({
      documents: [
        { id: "paper-1", kind: "prescription", size: 4, added_at: "2026-10-17T04:30:00Z" },
        { id: "paper-2", kind: "other", size: 4, added_at: "2026-10-17T04:31:00Z" },
      ],
      limit: 2,
    });
    renderPapers();

    expect(
      await screen.findByText(
        "You have added the most photos. Show any other papers to the doctor.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Prescription")).toBeVisible();
    expect(screen.getByRole("button", { name: "Remove photo 2: Other paper" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Test report" })).toBeNull();
  });

  it("explains a refused photo in the patient's language", async () => {
    await i18n.changeLanguage("hi");
    vi.spyOn(intake, "documents").mockResolvedValue({ documents: [], limit: 5 });
    vi.spyOn(intake, "addDocument").mockRejectedValue(
      new IntakeRequestError("UNSUPPORTED_IMAGE", "Not a photo", 415),
    );
    renderPapers();

    await userEvent.setup().upload(screen.getByTestId("papers-file"), photo());
    expect(
      await screen.findByText("यह फ़ाइल फ़ोटो नहीं है। कृपया कागज़ की फ़ोटो लें।"),
    ).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
