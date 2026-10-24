import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { DoctorNotesPanel } from "./DoctorNotesPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const version = (number: number, plan: string, by = "Dr Rao") => ({
  version: number,
  findings: "Throat red",
  assessment: "Viral pharyngitis likely",
  plan,
  follow_up_on: null,
  by_name: by,
  at: "2026-10-24T04:45:00Z",
});
const notes = (versions: ReturnType<typeof version>[], editable = true) => ({
  visit_id: "visit-1",
  version: versions[0]?.version ?? 0,
  editable,
  versions,
});
const json = (body: object, status = 200) => new Response(JSON.stringify(body), { status });

function signedInAs(role: "doctor" | "nurse") {
  vi.spyOn(staffAuth, "user", "get").mockReturnValue({
    id: `${role}-1`,
    name: role === "doctor" ? "Dr Rao" : "Nurse Patil",
    email: `${role}@example.test`,
    role,
    hospital_id: "hospital-1",
  });
}

function renderPanel() {
  return render(
    <I18nextProvider i18n={i18n}>
      <DoctorNotesPanel visitId="visit-1" />
    </I18nextProvider>,
  );
}

describe("Doctor's notes panel", () => {
  it("saves the first version with the version the doctor started from", async () => {
    signedInAs("doctor");
    const request = vi
      .spyOn(staffAuth, "request")
      .mockResolvedValueOnce(json(notes([])))
      .mockResolvedValueOnce(json(notes([version(1, "Fluids")])));
    renderPanel();

    const save = await screen.findByRole("button", { name: "Save notes" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Plan" }), {
      target: { value: "  Fluids " },
    });
    fireEvent.click(save);

    expect(await screen.findByRole("status")).toHaveTextContent("Notes saved (version 1).");
    const [path, init] = request.mock.calls[1];
    expect(path).toBe("/visits/visit-1/doctor-notes");
    expect(init?.method).toBe("PUT");
    expect((init?.headers as Record<string, string>)["If-Match"]).toBe('"0"');
    expect(JSON.parse(String(init?.body))).toEqual({
      findings: "",
      assessment: "",
      plan: "Fluids",
      follow_up_on: null,
    });
  });

  it("keeps the doctor's text when someone else saved meanwhile, and shows theirs", async () => {
    signedInAs("doctor");
    vi.spyOn(staffAuth, "request")
      .mockResolvedValueOnce(json(notes([version(1, "Fluids")])))
      .mockResolvedValueOnce(json({ code: "NOTES_CONFLICT" }, 409))
      .mockResolvedValueOnce(
        json(notes([version(2, "Rest at home", "Dr Shah"), version(1, "Fluids")])),
      );
    renderPanel();

    const plan = await screen.findByRole("textbox", { name: "Plan" });
    expect(plan).toHaveValue("Fluids");
    fireEvent.change(plan, { target: { value: "Paracetamol as needed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save notes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Someone saved these notes");
    expect(plan).toHaveValue("Paracetamol as needed");
    await waitFor(() => expect(screen.getAllByText(/Dr Shah/).length).toBeGreaterThan(0));
    expect(screen.getAllByText("Rest at home").length).toBeGreaterThan(0);
    expect(screen.getByText("Earlier versions (1)")).toBeInTheDocument();
  });

  it("shows nurses the notes without a form", async () => {
    signedInAs("nurse");
    vi.spyOn(staffAuth, "request").mockResolvedValueOnce(json(notes([version(1, "Fluids")])));
    renderPanel();

    expect(await screen.findByText("Fluids")).toBeInTheDocument();
    expect(screen.getByText(/Only a doctor of this department/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save notes" })).toBeNull();
  });

  it("tells a doctor when the notes have closed", async () => {
    signedInAs("doctor");
    vi.spyOn(staffAuth, "request").mockResolvedValueOnce(
      json(notes([version(1, "Fluids")], false)),
    );
    renderPanel();

    expect(await screen.findByText(/can no longer be changed/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
