import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { DoctorConsultation } from "./DoctorConsultation";

const sheet = {
  schema_version: "1.0",
  language: "en",
  chief_complaint: {
    text_original: "Synthetic headache",
    text_en: "Synthetic headache",
    confidence: 0.9,
  },
  hpi: { associated_symptoms: [], severity_0_10: 4 },
  routing: {
    department_code: "GENMED",
    confidence: 0.9,
    reason_en: "General medicine review",
    needs_nurse_confirmation: false,
  },
  red_flags: [],
  field_meta: {
    "/chief_complaint/text_original": {
      source: "patient_text",
      confidence: 0.9,
      confirmed: true,
    },
  },
  disclaimer: "History only. Not a diagnosis.",
};

function renderConsult(path = "/staff/consultations?visit_id=visit-1") {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/staff/consultations" element={<DoctorConsultation />} />
        </Routes>
      </MemoryRouter>
    </I18nextProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Phase 7.4 doctor consultation", () => {
  it("shows an empty state when no queue visit is selected", async () => {
    renderConsult("/staff/consultations");

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Choose a patient from the queue to open their consultation.",
    );
    expect(screen.getByRole("link", { name: "Back to queue" })).toBeVisible();
  });

  it("announces loading and provides retry after a case sheet read fails", async () => {
    let failSheetRequest: ((value: Response) => void) | undefined;
    vi.spyOn(staffAuth, "request").mockImplementation((path) => {
      if (path.endsWith("/case-sheet")) {
        return new Promise<Response>((resolve) => {
          failSheetRequest = resolve;
        });
      }
      return Promise.resolve(
        new Response(JSON.stringify({ visit_id: "visit-1", turns: [] }), {
          status: 200,
        }),
      );
    });

    renderConsult();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Loading the case sheet…",
    );
    failSheetRequest?.(new Response(null, { status: 503 }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The case sheet or transcript could not be loaded.",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("loads the case sheet and audited transcript and sends versioned edits", async () => {
    const request = vi.spyOn(staffAuth, "request");
    request
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            visit_id: "visit-1",
            version: 3,
            case_sheet: sheet,
          }),
          { status: 200, headers: { ETag: '"3"' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            visit_id: "visit-1",
            turns: [
              {
                turn_index: 1,
                question_id: "chief_complaint",
                input_mode: "text",
                text_original: "Synthetic headache",
                text_english: null,
                stt_confidence: null,
              },
            ],
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            visit_id: "visit-1",
            version: 4,
            case_sheet: sheet,
          }),
          { status: 200, headers: { ETag: '"4"' } },
        ),
      );

    renderConsult();
    expect((await screen.findAllByText("Synthetic headache")).length).toBe(2);
    expect(
      screen.getByRole("heading", { name: "Interview transcript" }),
    ).toBeInTheDocument();
    const editButtons = await screen.findAllByRole("button", { name: "Edit" });
    await userEvent.setup().click(editButtons[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "Chief complaint" }), {
      target: { value: "Synthetic severe headache" },
    });
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Save edit" }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    expect(request.mock.calls[2][0]).toBe("/visits/visit-1/case-sheet");
    expect(request.mock.calls[2][1]).toMatchObject({
      method: "PATCH",
      headers: {
        "Content-Type": "application/json-patch+json",
        "If-Match": '"3"',
      },
    });
    expect(request.mock.calls[2][1]?.body).toContain(
      "Synthetic severe headache",
    );
  });

  it("offers a reload and applies a draft against the latest version after a conflict", async () => {
    const request = vi.spyOn(staffAuth, "request");
    const sheetResponse = (version: number) =>
      new Response(
        JSON.stringify({ visit_id: "visit-1", version, case_sheet: sheet }),
        { status: 200, headers: { ETag: `"${version}"` } },
      );
    const transcriptResponse = () =>
      new Response(JSON.stringify({ visit_id: "visit-1", turns: [] }), {
        status: 200,
      });
    request
      .mockResolvedValueOnce(sheetResponse(3))
      .mockResolvedValueOnce(transcriptResponse())
      .mockResolvedValueOnce(new Response(null, { status: 409 }))
      .mockResolvedValueOnce(sheetResponse(4))
      .mockResolvedValueOnce(transcriptResponse())
      .mockResolvedValueOnce(sheetResponse(5));

    renderConsult();
    await screen.findByText("Synthetic headache");
    await userEvent
      .setup()
      .click((await screen.findAllByRole("button", { name: "Edit" }))[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "Chief complaint" }), {
      target: { value: "Updated synthetic headache" },
    });
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Save edit" }));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Apply my edit to latest" }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(6));
    expect(request.mock.calls[5][1]?.headers).toMatchObject({
      "If-Match": '"4"',
    });
    expect(request.mock.calls[5][1]?.body).toContain(
      "Updated synthetic headache",
    );
  });

  it("downloads the audited PDF through the authenticated API", async () => {
    const request = vi.spyOn(staffAuth, "request");
    request
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            visit_id: "visit-1",
            version: 1,
            case_sheet: sheet,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ visit_id: "visit-1", turns: [] }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(
        new Response(new Blob(["synthetic pdf"]), {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        }),
      );
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:synthetic-pdf");
    const downloadClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    renderConsult();
    await screen.findByText("Synthetic headache");
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Download case sheet PDF" }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    expect(request.mock.calls[2][0]).toBe("/visits/visit-1/case-sheet.pdf");
    expect(downloadClick).toHaveBeenCalledOnce();
  });
});
