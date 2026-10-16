import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import type { ConsultQueue } from "./consultQueue";
import { DoctorConsultation } from "./DoctorConsultation";

const queueState: { current: ConsultQueue } = {
  current: { items: [], state: "loading", now: 0, reload: () => undefined },
};
vi.mock("./ConsultPatientRail", () => ({ ConsultPatientRailView: () => null }));
vi.mock("./consultQueue", () => ({ useConsultQueue: () => queueState.current }));

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
    "/chief_complaint": {
      source: "patient_text",
      confidence: 0.9,
      confirmed: true,
      ai_assisted: true,
    },
  },
  patient_confirmation: {
    confirmed_by_patient: true,
    corrections: ["duration"],
    needs_assistance: false,
  },
  disclaimer: "History only. Not a diagnosis.",
};

function renderConsult(path = "/staff/consultations?visit_id=visit-1") {
  const router = createMemoryRouter(
    [
      { path: "/staff/consultations", element: <DoctorConsultation /> },
      { path: "/staff/queue", element: <p>Queue page</p> },
    ],
    { initialEntries: [path] },
  );
  return render(
    <I18nextProvider i18n={i18n}>
      <RouterProvider router={router} />
    </I18nextProvider>,
  );
}

const sheetResponse = (version: number, caseSheet: object = sheet) =>
  new Response(
    JSON.stringify({
      visit_id: "visit-1",
      version,
      case_sheet: caseSheet,
      confirmed_by_patient: true,
      confirmed_at: "2026-10-06T04:30:00Z",
    }),
    { status: 200, headers: { ETag: `"${version}"` } },
  );
const transcriptResponse = (turns: object[] = []) =>
  new Response(JSON.stringify({ visit_id: "visit-1", turns }), { status: 200 });
const emptyHistory = {
  visit_id: "visit-1",
  opd_number: "26-000007",
  linked_by: null,
  follow_up_of: null,
  previous_visits: [],
  known_allergies: [],
  known_medications: [],
  known_conditions: [],
  no_known_allergies_reported: null,
};
const historyResponse = (history: object = emptyHistory) =>
  new Response(JSON.stringify(history), { status: 200 });

/**
 * Answer the consultation's requests in order. The earlier-visits panel loads on its own
 * and gets its own answer, so it never takes a response meant for the case sheet.
 */
function mockRequests(responses: Response[], history: object | (() => Response) = emptyHistory) {
  const queue = [...responses];
  const request = vi.spyOn(staffAuth, "request").mockImplementation((path) =>
    Promise.resolve(
      path.endsWith("/history")
        ? typeof history === "function"
          ? history()
          : historyResponse(history)
        : (queue.shift() ?? new Response(null, { status: 500 })),
    ),
  );
  const calls = () => request.mock.calls.filter(([path]) => !String(path).endsWith("/history"));
  return { request, calls };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  queueState.current = { items: [], state: "loading", now: 0, reload: () => undefined };
});

describe("Phase 7.4 doctor consultation", () => {
  it("shows an empty state when no queue visit is selected", async () => {
    renderConsult("/staff/consultations");

    expect(
      await screen.findByRole("heading", { name: "Select a patient to begin" }),
    ).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Pick a patient from Today's patients to open their case sheet.",
    );
    expect(screen.getByRole("link", { name: "Back to queue" })).toBeVisible();
  });

  it("says when nobody is waiting and offers a refresh", async () => {
    const reload = vi.fn();
    queueState.current = { items: [], state: "ready", now: 0, reload };
    renderConsult("/staff/consultations");

    expect(await screen.findByRole("heading", { name: "No patients waiting" })).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Refresh list" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("announces loading and provides retry after a case sheet read fails", async () => {
    let failSheetRequest: ((value: Response) => void) | undefined;
    vi.spyOn(staffAuth, "request").mockImplementation((path) => {
      if (path.endsWith("/case-sheet")) {
        return new Promise<Response>((resolve) => {
          failSheetRequest = resolve;
        });
      }
      return Promise.resolve(transcriptResponse());
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

  it("opens on a focused overview and keeps the full record in the other sections", async () => {
    const { calls } = mockRequests([
      sheetResponse(3),
      transcriptResponse([
        {
          turn_index: 1,
          question_id: "chief_complaint",
          input_mode: "text",
          text_original: "Synthetic headache since two days, worse in the evening",
          text_english: null,
          stt_confidence: null,
        },
      ]),
      sheetResponse(4),
    ]);

    renderConsult();
    expect(await screen.findByText("Synthetic headache")).toBeVisible();
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/^Confirmed by patient at/)).toBeVisible();
    expect(screen.getByText("Patient corrections: duration")).toBeVisible();
    expect(
      screen.getByText(
        "Some history fields were organized with AI assistance and need staff review.",
      ),
    ).toBeVisible();
    // The interview and editing controls are not on the overview.
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByText(/worse in the evening/)).toBeNull();

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /Interview transcript/ }));
    expect(screen.getByRole("heading", { name: "Interview transcript" })).toBeVisible();
    expect(
      screen.getByText("Synthetic headache since two days, worse in the evening"),
    ).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "Patient history" }));
    expect(screen.getByText("AI-assisted")).toBeVisible();
    expect(screen.getByText("Patient text")).toBeVisible();
    expect(screen.getByText("90%")).toBeVisible();
    const editButtons = await screen.findAllByRole("button", { name: "Edit" });
    await user.click(editButtons[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "Chief complaint" }), {
      target: { value: "Synthetic severe headache" },
    });
    await user.click(screen.getByRole("button", { name: "Save edit" }));

    await waitFor(() => expect(calls()).toHaveLength(3));
    expect(calls()[2][0]).toBe("/visits/visit-1/case-sheet");
    expect(calls()[2][1]).toMatchObject({
      method: "PATCH",
      headers: {
        "Content-Type": "application/json-patch+json",
        "If-Match": '"3"',
      },
    });
    expect(calls()[2][1]?.body).toContain("Synthetic severe headache");
  });

  it("keeps earlier visits in their own section, labelled by source", async () => {
    mockRequests([sheetResponse(1), transcriptResponse()], {
      ...emptyHistory,
      linked_by: "card",
      follow_up_of: "visit-0",
      previous_visits: [
        {
          visit_id: "visit-0",
          service_date: "2026-10-03",
          department: "General Medicine",
          status: "done",
          token: "MED-004",
          urgency: "routine",
          chief_complaint: "Fever and cough",
          ai_brief: "Three days of fever with dry cough.",
          staff_edited: true,
          red_flags: [],
          conditions: ["Diabetes"],
          medications: ["Metformin"],
          allergies: ["Penicillin"],
          no_known_allergies: false,
        },
      ],
      known_allergies: [{ text: "Penicillin", last_reported: "2026-10-03", visits: 1 }],
      known_medications: [{ text: "Metformin", last_reported: "2026-10-03", visits: 1 }],
      known_conditions: [{ text: "Diabetes", last_reported: "2026-10-03", visits: 1 }],
    });

    renderConsult();
    const tab = await screen.findByRole("tab", { name: /Previous visits/ });
    await waitFor(() => expect(tab).toHaveTextContent("1"));
    await userEvent.setup().click(tab);
    expect(screen.getByText("Linked by the patient's OPD card")).toBeVisible();
    expect(screen.getByText(/Follow-up of the visit on .*: Fever and cough/)).toBeVisible();
    expect(screen.getByText("Penicillin")).toBeVisible();
    expect(
      screen.getByText("Patient-reported at earlier visits. Confirm with the patient today."),
    ).toBeVisible();
    expect(screen.getByText("AI draft")).toBeVisible();
    expect(screen.getByText("Edited by staff")).toBeVisible();
    // Today's interview stays in its own section.
    expect(screen.queryByText("Synthetic headache")).toBeNull();
  });

  it("explains that earlier visits close with the visit", async () => {
    mockRequests([sheetResponse(1), transcriptResponse()], () =>
      new Response(JSON.stringify({ code: "HISTORY_CLOSED" }), { status: 403 }),
    );

    renderConsult();
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Previous visits/ }));
    expect(
      await screen.findByText(
        "Earlier visits are shown only while the patient is under your care today.",
      ),
    ).toBeVisible();
  });

  it("puts red flags first, as reported information to verify", async () => {
    mockRequests([
      sheetResponse(1, {
        ...sheet,
        red_flags: [
          {
            id: "RF_HAEMOPTYSIS",
            level: "urgent",
            source: "rule",
            evidence: "blood in phlegm",
            reason_en: "Blood in phlegm reported",
          },
        ],
      }),
      transcriptResponse(),
    ]);

    renderConsult();
    const flags = await screen.findByRole("region", { name: "Red flags — review required" });
    expect(flags).toHaveTextContent("Blood in phlegm reported");
    expect(flags).toHaveTextContent("Patient said: “blood in phlegm”");
    expect(flags).toHaveTextContent("From a safety rule");
    expect(flags).toHaveTextContent("this is not a diagnosis");
    expect(screen.getByRole("tab", { name: /Overview/ })).toHaveTextContent("1");
  });

  it("moves between sections with the arrow keys", async () => {
    mockRequests([sheetResponse(1), transcriptResponse()]);

    renderConsult();
    const overview = await screen.findByRole("tab", { name: "Overview" });
    overview.focus();
    fireEvent.keyDown(overview, { key: "ArrowRight" });
    const history = screen.getByRole("tab", { name: "Patient history" });
    expect(history).toHaveAttribute("aria-selected", "true");
    expect(history).toHaveFocus();
    fireEvent.keyDown(history, { key: "End" });
    expect(screen.getByRole("tab", { name: "Clinical notes" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText(/are not stored in OPD Saathi yet/)).toBeVisible();
  });

  it("asks before an unsaved edit is lost by leaving the page", async () => {
    mockRequests([sheetResponse(1), transcriptResponse()]);

    renderConsult();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Patient history" }));
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "Chief complaint" }), {
      target: { value: "Half-typed correction" },
    });

    fireEvent.keyDown(window, { key: "n" });
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Discard your unsaved edit?");
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByRole("textbox", { name: "Chief complaint" })).toHaveValue(
      "Half-typed correction",
    );

    fireEvent.keyDown(window, { key: "n" });
    await user.click(await screen.findByRole("button", { name: "Discard and leave" }));
    expect(await screen.findByText("Queue page")).toBeVisible();
  });

  it("offers a reload and applies a draft against the latest version after a conflict", async () => {
    const { calls } = mockRequests([
      sheetResponse(3),
      transcriptResponse(),
      new Response(null, { status: 409 }),
      sheetResponse(4),
      transcriptResponse(),
      sheetResponse(5),
    ]);

    renderConsult();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Patient history" }));
    await user.click((await screen.findAllByRole("button", { name: "Edit" }))[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "Chief complaint" }), {
      target: { value: "Updated synthetic headache" },
    });
    await user.click(screen.getByRole("button", { name: "Save edit" }));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Apply my edit to latest" }));

    await waitFor(() => expect(calls()).toHaveLength(6));
    expect(calls()[5][1]?.headers).toMatchObject({ "If-Match": '"4"' });
    expect(calls()[5][1]?.body).toContain("Updated synthetic headache");
  });

  it("downloads the audited PDF through the authenticated API", async () => {
    const { calls } = mockRequests([
      sheetResponse(1),
      transcriptResponse(),
      new Response(new Blob(["synthetic pdf"]), {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      }),
    ]);
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:synthetic-pdf");
    const downloadClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    renderConsult();
    await screen.findByText("Synthetic headache");
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Download case sheet PDF" }));

    await waitFor(() => expect(calls()).toHaveLength(3));
    expect(calls()[2][0]).toBe("/visits/visit-1/case-sheet.pdf");
    expect(downloadClick).toHaveBeenCalledOnce();
  });
});
