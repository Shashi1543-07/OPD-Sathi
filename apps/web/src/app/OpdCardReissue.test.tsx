import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { OpdCardReissue } from "./OpdCardReissue";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const candidate = {
  opd_number: "26-000042",
  age_years: 41,
  sex: "female",
  visit_count: 3,
  last_visit: "2026-10-01",
  card_version: 1,
};
const json = (body: object, status = 200) => new Response(JSON.stringify(body), { status });

async function findPatient() {
  render(
    <I18nextProvider i18n={i18n}>
      <OpdCardReissue />
    </I18nextProvider>,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "OPD number" }), {
    target: { value: "26-000042" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByText("26-000042");
}

describe("Lost OPD card", () => {
  it("re-issues only after the desk confirms, and shows the new PIN once", async () => {
    const request = vi
      .spyOn(staffAuth, "request")
      .mockResolvedValueOnce(json({ items: [candidate] }))
      .mockResolvedValueOnce(json({ opd_number: "26-000042", pin: "482913", card_version: 2 }));
    await findPatient();

    fireEvent.click(screen.getByRole("button", { name: "Re-issue card" }));
    expect(screen.getByText(/old PIN will stop working/)).toBeVisible();
    expect(request).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Yes, re-issue" }));

    expect(await screen.findByText("482913")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("New card for 26-000042. PIN:");
    const [path, init] = request.mock.calls[1];
    expect(path).toBe("/patients/card/reissue");
    expect(JSON.parse(String(init?.body))).toEqual({ opd_number: "26-000042", card_version: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("482913")).toBeNull();
  });

  it("says so when another desk re-issued the card first", async () => {
    vi.spyOn(staffAuth, "request")
      .mockResolvedValueOnce(json({ items: [candidate] }))
      .mockResolvedValueOnce(json({ code: "CARD_CHANGED" }, 409));
    await findPatient();

    fireEvent.click(screen.getByRole("button", { name: "Re-issue card" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, re-issue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("re-issued meanwhile");
  });
});
