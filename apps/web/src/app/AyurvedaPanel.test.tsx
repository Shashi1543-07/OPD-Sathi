import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { AyurvedaPanel } from "./AyurvedaPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const ayush = {
  appetite: { text_original: "पहले से कम", text_en: "Less than before" },
  digestion: { text_original: "Light and fine", text_en: "Light and fine" },
  examination: { nadi: "Vata predominant", jihva: "Coated" },
};

function renderPanel(canEdit: boolean, handlers: { onSaved?: () => void; onConflict?: () => void } = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <AyurvedaPanel
        visitId="visit-1"
        ayush={ayush as never}
        version={4}
        canEdit={canEdit}
        onSaved={handlers.onSaved ?? (() => undefined)}
        onConflict={handlers.onConflict ?? (() => undefined)}
      />
    </I18nextProvider>,
  );
}

describe("Ayurveda history panel", () => {
  it("shows what the patient reported, with their own words beside the English", () => {
    renderPanel(false);
    expect(screen.getByText("Appetite (Ahara Shakti)")).toBeInTheDocument();
    expect(screen.getByText("Less than before")).toBeInTheDocument();
    expect(screen.getByText("(पहले से कम)")).toBeInTheDocument();
    // Read-only for staff who cannot edit the case sheet.
    expect(screen.getByRole("textbox", { name: "Nadi (pulse)" })).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Save examination" })).toBeNull();
  });

  it("saves the vaidya's findings as one versioned edit", async () => {
    const onSaved = vi.fn();
    const request = vi.spyOn(staffAuth, "request").mockResolvedValue(
      new Response(JSON.stringify({ version: 5, case_sheet: {} }), {
        status: 200,
        headers: { ETag: '"5"' },
      }),
    );
    renderPanel(true, { onSaved });

    fireEvent.change(screen.getByRole("textbox", { name: "Nadi (pulse)" }), {
      target: { value: "Pitta predominant" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Jihva (tongue)" }), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Prakriti (constitution)" }), {
      target: { value: "Vata-Pitta" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save examination" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ version: 5 })));
    expect(request).toHaveBeenCalledWith(
      "/visits/visit-1/case-sheet",
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({ "If-Match": '"4"' }),
        body: JSON.stringify([
          { op: "replace", path: "/ayush/examination/nadi", value: "Pitta predominant" },
          { op: "remove", path: "/ayush/examination/jihva" },
          { op: "add", path: "/ayush/examination/prakriti", value: "Vata-Pitta" },
        ]),
      }),
    );
    expect(await screen.findByText("Examination saved.")).toHaveAttribute("role", "status");
  });

  it("reloads the sheet on a clash and keeps the vaidya's entries", async () => {
    const onConflict = vi.fn();
    vi.spyOn(staffAuth, "request").mockResolvedValue(new Response(null, { status: 409 }));
    renderPanel(true, { onConflict });
    const nadi = screen.getByRole("textbox", { name: "Nadi (pulse)" });
    fireEvent.change(nadi, { target: { value: "Kapha" } });
    fireEvent.click(screen.getByRole("button", { name: "Save examination" }));
    expect(
      await screen.findByText(
        "The case sheet changed and has been reloaded. Your entries are kept; save again.",
      ),
    ).toHaveAttribute("role", "alert");
    expect(onConflict).toHaveBeenCalled();
    expect(nadi).toHaveValue("Kapha");
  });
});
