import { cleanup, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { ConsultPatientRail } from "./ConsultPatientRail";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const item = (overrides: Record<string, unknown>) => ({
  visit_id: "visit-1",
  department_id: "dept-1",
  token: "GEN-001",
  urgency: "routine",
  status: "queued",
  age_years: 52,
  sex: "male",
  complaint: "fever, cough",
  position: 1,
  queued_at: new Date().toISOString(),
  ...overrides,
});

describe("consultation patient rail", () => {
  it("lists today's patients and highlights the open one", async () => {
    vi.spyOn(staffAuth, "request").mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            item({}),
            item({
              visit_id: "visit-2",
              token: "GEN-002",
              urgency: "urgent",
              status: "called",
              position: 2,
              complaint: "chest pain",
            }),
          ],
        }),
        { status: 200 },
      ),
    );
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <ConsultPatientRail activeVisitId="visit-2" />
        </MemoryRouter>
      </I18nextProvider>,
    );

    expect(await screen.findByText("Fever, cough")).toBeInTheDocument();
    expect(screen.getByText("2 active patients")).toBeInTheDocument();
    const active = screen.getByRole("link", { current: "page" });
    expect(active).toHaveTextContent("GEN-002");
    expect(active).toHaveAttribute(
      "href",
      "/staff/consultations?visit_id=visit-2&status=called",
    );
    expect(active).toHaveTextContent("Needs early attention");
  });

  it("explains when the list cannot load", async () => {
    vi.spyOn(staffAuth, "request").mockResolvedValue(new Response("", { status: 500 }));
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <ConsultPatientRail activeVisitId={null} />
        </MemoryRouter>
      </I18nextProvider>,
    );
    expect(
      await screen.findByText("Could not load the patient list. Try refresh."),
    ).toBeInTheDocument();
  });
});
