import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { staffAuth } from "../lib/auth";
import { StaffQueueBoard } from "./StaffQueueBoard";
import {
  applyQueueOperations,
  sequenceAction,
  type QueueItem,
} from "./queueBoardModel";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 196,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        index,
        start: index * 196,
      })),
    measureElement: () => undefined,
  }),
}));

const departments = [
  { id: "dept-medicine", code: "GENMED", name: "General Medicine" },
  { id: "dept-pediatrics", code: "PED", name: "Pediatrics" },
];

function item(overrides: Partial<QueueItem>): QueueItem {
  return {
    visit_id: "visit-1",
    department_id: departments[0].id,
    token: "MED-001",
    urgency: "routine",
    status: "queued",
    age_years: 42,
    sex: "female",
    complaint: "Headache",
    position: 1,
    queued_at: "2026-10-06T08:00:00+05:30",
    routing: null,
    ...overrides,
  };
}

function response(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 });
}

function renderBoard() {
  return render(
    <I18nextProvider i18n={i18n}>
      <StaffQueueBoard />
    </I18nextProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("Phase 7.2 queue board", () => {
  it("announces queue loading and distinguishes an empty department list", async () => {
    let finishQueueRequest: ((value: Response) => void) | undefined;
    vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/alerts")) return response({ items: [] });
      return new Promise<Response>((resolve) => {
        finishQueueRequest = resolve;
      });
    });

    renderBoard();
    expect(await screen.findByText("Loading the staff queue…")).toHaveAttribute(
      "role",
      "status",
    );
    finishQueueRequest?.(
      response({ items: [], departments: [], next_cursor: null }),
    );
    expect(
      await screen.findByText(
        "No active departments are available for your account.",
      ),
    ).toHaveAttribute("role", "status");
  });

  it("shows a retryable alert state when the initial queue request fails", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/alerts")) return response({ items: [] });
      throw new Error("Queue is temporarily unavailable.");
    });

    renderBoard();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Queue is temporarily unavailable.",
    );
    expect(screen.queryByText("Loading the staff queue…")).toBeNull();
  });

  it("ignores stale sequence numbers, applies the next event, and resyncs on gaps", () => {
    expect(sequenceAction(8, 8)).toBe("ignore");
    expect(sequenceAction(8, 9)).toBe("apply");
    expect(sequenceAction(8, 11)).toBe("resync");

    const current = item({ visit_id: "visit-1", urgency: "routine" });
    const result = applyQueueOperations(
      [current],
      [
        {
          op: "upsert",
          item: {
            visit_id: "visit-1",
            token: "MED-009",
            urgency: "urgent",
            status: "queued",
          },
        },
      ],
      "all",
    );
    expect(result.items[0]).toMatchObject({
      token: "MED-009",
      urgency: "urgent",
    });
    expect(result.needsResync).toBe(false);
  });

  it("removes rows that stop matching an active status filter and resyncs for unseen rows", () => {
    const current = item({ visit_id: "visit-1", status: "called" });
    const filtered = applyQueueOperations(
      [current],
      [
        {
          op: "upsert",
          item: {
            visit_id: "visit-1",
            token: "MED-001",
            urgency: "routine",
            status: "queued",
          },
        },
      ],
      "called",
    );
    expect(filtered.items).toEqual([]);

    const removedFromWaitingSet = applyQueueOperations(
      [current],
      [{ op: "remove", visit_id: "visit-1" }],
      "all",
    );
    expect(removedFromWaitingSet.items).toEqual([current]);
    expect(removedFromWaitingSet.needsResync).toBe(true);

    const missing = applyQueueOperations(
      [],
      [
        {
          op: "upsert",
          item: {
            visit_id: "visit-new",
            token: "MED-002",
            urgency: "urgent",
            status: "queued",
          },
        },
      ],
      "all",
    );
    expect(missing.needsResync).toBe(true);
  });

  it("pins urgent patients and shows the staff queue columns and live state", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/alerts")) return response({ items: [] });
      return response({
        items: [
          item({ visit_id: "routine", token: "MED-001", position: 1 }),
          item({
            visit_id: "urgent",
            token: "MED-002",
            urgency: "urgent",
            position: 2,
          }),
        ],
        departments,
        next_cursor: "more",
      });
    });

    renderBoard();

    // Tokens also appear in the "Next to call" panel, so look inside the queue list.
    const queue = await screen.findByRole("region", { name: "Queue patients" });
    expect(await within(queue).findByText("MED-002")).toBeInTheDocument();
    const tokens = within(queue).getAllByText(/^MED-00[12]$/);
    expect(tokens[0]).toHaveTextContent("MED-002");
    expect(tokens[1]).toHaveTextContent("MED-001");
    expect(screen.getByText("Complaint")).toBeInTheDocument();
    expect(screen.getAllByText("Headache")).toHaveLength(2);
    expect(
      screen.getByRole("button", { name: "Load more patients" }),
    ).toBeEnabled();
  });

  it("requests the selected department and status filter and loads the next page", async () => {
    const request = vi
      .spyOn(staffAuth, "request")
      .mockImplementation(async (path) => {
        if (path.startsWith("/alerts")) return response({ items: [] });
        const query = new URLSearchParams(path.split("?")[1]);
        const selected = query.get("department_id") === departments[1].id;
        const pageTwo = query.has("cursor");
        return response({
          items: [
            item({
              visit_id: pageTwo
                ? "pediatric-visit-next"
                : selected
                  ? "pediatric-visit"
                  : "medicine-visit",
              department_id: selected ? departments[1].id : departments[0].id,
              token: pageTwo ? "PED-003" : selected ? "PED-002" : "MED-001",
              status:
                (query.get("status") as QueueItem["status"] | null) ?? "queued",
            }),
          ],
          departments,
          next_cursor: query.has("cursor") ? null : "next-page",
        });
      });

    renderBoard();
    const queue = await screen.findByRole("region", { name: "Queue patients" });
    await within(queue).findByText("MED-001");
    // Departments are a dropdown now (the old button row was clipped at the right edge).
    const department = screen.getByRole("combobox", { name: "Department" });
    const pediatrics = within(department).getByRole("option", { name: "Pediatrics" });
    fireEvent.change(department, { target: { value: (pediatrics as HTMLOptionElement).value } });
    await within(await screen.findByRole("region", { name: "Queue patients" })).findByText("PED-002");
    fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
      target: { value: "called" },
    });
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        expect.stringContaining("status=called"),
        expect.anything(),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Load more patients" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        expect.stringContaining("cursor=next-page"),
        expect.anything(),
      ),
    );
  });

  it("persists the alert sound preference", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async (path) =>
      path.startsWith("/alerts")
        ? response({ items: [] })
        : response({
            items: [],
            departments: [departments[0]],
            next_cursor: null,
          }),
    );
    renderBoard();
    const sound = screen.getByRole("checkbox", { name: "Alert sound" });
    fireEvent.click(sound);
    await waitFor(() =>
      expect(window.localStorage.getItem("opd-staff-alert-sound")).toBe(
        "enabled",
      ),
    );
  });
});
