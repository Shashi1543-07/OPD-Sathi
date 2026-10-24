import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { DocumentsPanel } from "./DocumentsPanel";
import type { StaffDocument, VisitDocuments } from "./visitDocuments";

beforeEach(() => {
  // jsdom has no object URLs; the panel shows each photo through one.
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:synthetic-photo"),
  });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function signedIn(role: "doctor" | "nurse" | "admin") {
  vi.spyOn(staffAuth, "user", "get").mockReturnValue({
    id: `${role}-1`,
    name: `Synthetic ${role}`,
    email: `${role}@example.test`,
    role,
    hospital_id: "hospital-1",
  });
}

const paper = (overrides: Partial<StaffDocument> = {}): StaffDocument => ({
  id: "paper-1",
  visit_id: "visit-1",
  service_date: "2026-10-17",
  kind: "lab_report",
  source: "patient",
  added_by: null,
  added_at: "2026-10-17T04:30:00Z",
  size: 2048,
  document_date: "2026-09-30",
  reading_status: "complete",
  reading: {
    readable: true,
    document_type: "lab_report",
    document_date: "2026-09-30",
    facility: "Synthetic Diagnostics",
    diagnoses: [],
    medicines: [],
    lab_results: [
      {
        test: "Haemoglobin",
        value: "9.8",
        unit: "g/dL",
        reference_range: "12.0 - 15.0",
        printed_flag: "low",
        range_flag: "below",
      },
      {
        test: "Urine culture",
        value: "No growth",
        unit: "",
        reference_range: "",
        printed_flag: null,
        range_flag: null,
      },
    ],
    procedures: [],
  },
  ai_withdrawn: false,
  review_status: "unreviewed",
  reviewed_by: null,
  reviewed_at: null,
  ...overrides,
});

const papers = (overrides: Partial<VisitDocuments> = {}): VisitDocuments => ({
  visit_id: "visit-1",
  documents: [paper()],
  earlier: [],
  earlier_shown: true,
  limit: 10,
  ...overrides,
});

function renderPanel(documents: VisitDocuments | undefined, onChanged = vi.fn(), failed = false) {
  render(
    <I18nextProvider i18n={i18n}>
      <DocumentsPanel
        visitId="visit-1"
        documents={documents}
        failed={failed}
        onChanged={onChanged}
      />
    </I18nextProvider>,
  );
  return onChanged;
}

const photoResponse = () =>
  new Response(new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" }), {
    status: 200,
  });

describe("Old papers in the consultation", () => {
  it("shows the photo beside what AI read, compared only with the printed range", async () => {
    signedIn("doctor");
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(() => Promise.resolve(photoResponse()));
    renderPanel(papers());

    expect(await screen.findByRole("img", { name: "Photo of the paper: Test report" })).toHaveAttribute(
      "src",
      "blob:synthetic-photo",
    );
    expect(request).toHaveBeenCalledWith("/visits/visit-1/documents/paper-1/content", {
      cache: "no-store",
    });
    expect(screen.getByText("AI reading · compare with the photo")).toBeVisible();
    expect(screen.getByText(/^Dated .*2026 on the paper$/)).toBeVisible();
    const table = screen.getByRole("table");
    const haemoglobin = within(table).getByRole("row", { name: /Haemoglobin/ });
    expect(haemoglobin).toHaveTextContent("Below the printed range · Marked low on the report");
    // A text result is shown as written and never judged.
    expect(within(table).getByRole("row", { name: /Urine culture/ })).toHaveTextContent("—");
    expect(
      screen.getByText("Each result is compared only with the range printed beside it on this paper."),
    ).toBeVisible();
  });

  it("records the doctor's decision after comparing with the photo", async () => {
    signedIn("doctor");
    const request = vi.spyOn(staffAuth, "request").mockImplementation((path) =>
      Promise.resolve(
        path.endsWith("/review")
          ? new Response(JSON.stringify(paper({ review_status: "accepted" })), { status: 200 })
          : photoResponse(),
      ),
    );
    const onChanged = renderPanel(papers());

    await userEvent.setup().click(screen.getByRole("button", { name: "Matches the photo" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(request).toHaveBeenCalledWith(
      "/visits/visit-1/documents/paper-1/review",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ decision: "accepted" }) }),
    );
  });

  it("leaves the decision to doctors and hides a rejected or withdrawn reading", async () => {
    signedIn("nurse");
    vi.spyOn(staffAuth, "request").mockImplementation(() => Promise.resolve(photoResponse()));
    renderPanel(
      papers({
        documents: [
          paper({ id: "paper-1", review_status: "rejected", reviewed_by: "Dr Rao" }),
          paper({ id: "paper-2", ai_withdrawn: true, reading: null }),
          paper({ id: "paper-3", reading_status: "skipped", reading: null }),
        ],
      }),
    );
    expect(screen.queryByRole("button", { name: "Matches the photo" })).toBeNull();
    expect(screen.getByText("Rejected by Dr Rao: does not match the photo")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
    expect(
      screen.getByText(
        "The patient stopped AI help, so what AI read is not shown. Read the photo.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Not read by AI (the patient did not allow AI help, or AI is off). Read the photo.",
      ),
    ).toBeVisible();
    // A nurse can still photograph papers the patient shows at the desk.
    expect(screen.getByRole("button", { name: "Photograph a paper" })).toBeEnabled();
    await screen.findAllByRole("img");
  });

  it("adds a paper photographed at the desk", async () => {
    signedIn("nurse");
    const request = vi.spyOn(staffAuth, "request").mockImplementation((path) =>
      Promise.resolve(
        path.includes("?kind=")
          ? new Response(JSON.stringify(paper({ id: "paper-9", source: "staff" })), { status: 201 })
          : photoResponse(),
      ),
    );
    const onChanged = renderPanel(papers({ documents: [] }));
    const user = userEvent.setup();

    expect(screen.getByText("No papers were added at this visit.")).toBeVisible();
    await user.selectOptions(screen.getByRole("combobox", { name: "Kind of paper" }), "discharge_summary");
    const photo = new File([new Uint8Array([0xff, 0xd8, 0xff])], "paper.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByTestId("paper-file"), photo);

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(request).toHaveBeenCalledWith(
      "/visits/visit-1/documents?kind=discharge_summary",
      expect.objectContaining({ method: "POST", headers: { "Content-Type": "image/jpeg" } }),
    );
    expect(screen.getByText("Photo added.")).toBeVisible();
  });

  it("explains why a photo was refused", async () => {
    signedIn("doctor");
    vi.spyOn(staffAuth, "request").mockResolvedValue(
      new Response(JSON.stringify({ code: "VISIT_CLOSED" }), { status: 409 }),
    );
    const onChanged = renderPanel(papers({ documents: [] }));
    const photo = new File([new Uint8Array([0xff, 0xd8, 0xff])], "paper.jpg", { type: "image/jpeg" });
    await userEvent.setup().upload(screen.getByTestId("paper-file"), photo);

    expect(
      await screen.findByText("Papers can be added only while the patient is under care."),
    ).toBeVisible();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("shows earlier papers only while the patient is under care", () => {
    signedIn("doctor");
    renderPanel(papers({ documents: [], earlier: [], earlier_shown: false }));
    expect(
      screen.getByText(
        "Papers from earlier visits are shown only while the patient is under your care today.",
      ),
    ).toBeVisible();
  });

  it("never blocks the case sheet when papers cannot be loaded", () => {
    signedIn("doctor");
    renderPanel(undefined, vi.fn(), true);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Papers could not be loaded. Today's case sheet is not affected.",
    );
  });

  it("shows accepted results over time, oldest first, and says what still awaits review", async () => {
    signedIn("doctor");
    const result = (value: string, range_flag: "below" | "within") => ({
      test: "Haemoglobin",
      value,
      unit: "g/dL",
      reference_range: "12.0 - 15.0",
      printed_flag: null,
      range_flag,
    });
    const timeline = {
      visit_id: "visit-1",
      earlier_shown: true,
      awaiting_review: 2,
      papers: [
        {
          document_id: "old",
          visit_id: "visit-0",
          date: "2026-07-01",
          date_source: "printed",
          kind: "lab_report",
          facility: "Synthetic Diagnostics",
          lab_results: [result("9.8", "below")],
        },
        {
          document_id: "new",
          visit_id: "visit-1",
          date: "2026-10-17",
          date_source: "visit",
          kind: "lab_report",
          facility: "",
          lab_results: [result("12.9", "within")],
        },
      ],
      tests: [
        {
          test: "Haemoglobin",
          results: [
            { date: "2026-07-01", document_id: "old", result: result("9.8", "below") },
            { date: "2026-10-17", document_id: "new", result: result("12.9", "within") },
          ],
        },
      ],
    };
    vi.spyOn(staffAuth, "request").mockImplementation((path) =>
      Promise.resolve(
        path.endsWith("/investigations")
          ? new Response(JSON.stringify(timeline), { status: 200 })
          : photoResponse(),
      ),
    );
    renderPanel(papers());

    const section = await screen.findByRole("region", { name: "Results over time" });
    const table = within(section).getByRole("table", { name: "Haemoglobin" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
      "9.8 g/dL",
      "12.9 g/dL",
    ]);
    expect(rows[0]).toHaveTextContent("Below the printed range");
    expect(
      within(section).getByText(
        "2 papers are waiting for a doctor's decision and are not included.",
      ),
    ).toBeVisible();
    expect(within(section).getByText(/visit date; no date read on the paper/)).toBeVisible();
  });
});
