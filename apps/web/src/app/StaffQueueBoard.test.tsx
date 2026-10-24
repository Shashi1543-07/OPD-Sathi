import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
      <MemoryRouter initialEntries={["/staff/queue"]}>
        <Routes>
          <Route path="/staff/queue" element={<StaffQueueBoard />} />
          <Route path="/staff/consultations" element={<p>Case sheet open</p>} />
        </Routes>
      </MemoryRouter>
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

  it("keeps missed calls apart from the line and recalls a patient who comes back", async () => {
    const request = vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/alerts")) return response({ items: [] });
      if (path === "/queue/visit-missed/recall") return response({ status: "queued" });
      return response({
        items: [item({ visit_id: "visit-waiting", token: "MED-003" })],
        departments,
        next_cursor: null,
        missed: [
          {
            visit_id: "visit-missed",
            department_id: departments[0].id,
            token: "MED-001",
            urgency: "routine",
            age_years: 60,
            sex: "male",
            complaint: "dizziness",
            skip_count: 1,
            skipped_at: "2026-10-16T05:00:00Z",
            queued_at: "2026-10-16T04:30:00Z",
          },
        ],
        call_grace_seconds: 180,
      });
    });

    renderBoard();
    const missed = await screen.findByRole("region", { name: "Missed calls" });
    expect(within(missed).getByText("MED-001")).toBeInTheDocument();
    expect(within(missed).getByText(/Missed 1 call/)).toBeInTheDocument();
    // The missed patient is not in the line itself.
    const queue = screen.getByRole("region", { name: "Queue patients" });
    expect(within(queue).queryByText("MED-001")).toBeNull();

    fireEvent.click(within(missed).getByRole("button", { name: "Recall" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/queue/visit-missed/recall",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "Idempotency-Key": expect.any(String) }),
        }),
      ),
    );
  });

  it("lets the server pick the next patient and says who was called", async () => {
    const request = vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/alerts")) return response({ items: [] });
      if (path.startsWith("/queue/call-next")) return response({ status: "called", token: "MED-002" });
      return response({
        items: [item({ visit_id: "visit-2", token: "MED-002" })],
        departments,
        next_cursor: null,
        missed: [],
        call_grace_seconds: 180,
      });
    });

    renderBoard();
    fireEvent.click(await screen.findByRole("button", { name: "Call next" }));
    expect(await screen.findByText("Called MED-002.")).toHaveAttribute("role", "status");
    expect(request).toHaveBeenCalledWith(
      `/queue/call-next?department_id=${departments[0].id}`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("lets a nurse mark a called patient absent only after the grace period", async () => {
    vi.spyOn(staffAuth, "user", "get").mockReturnValue({
      id: "nurse-1",
      name: "Synthetic Nurse",
      email: "nurse@example.test",
      role: "nurse",
      hospital_id: "hospital-1",
    });
    const calledAt = (secondsAgo: number) => new Date(Date.now() - secondsAgo * 1000).toISOString();
    const request = vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/alerts")) return response({ items: [] });
      if (path.endsWith("/skip")) return response({ status: "skipped" });
      return response({
        items: [
          item({ visit_id: "just-called", token: "MED-004", status: "called", called_at: calledAt(30), called_by_name: "Dr Rao" }),
          item({ visit_id: "long-called", token: "MED-005", status: "called", called_at: calledAt(400), position: 2 }),
        ],
        departments,
        next_cursor: null,
        missed: [],
        call_grace_seconds: 180,
      });
    });

    renderBoard();
    const queue = await screen.findByRole("region", { name: "Queue patients" });
    const waiting = await within(queue).findByRole("button", { name: /Mark absent in 2:/ });
    expect(waiting).toBeDisabled();
    expect(within(queue).getByText("Called by Dr Rao")).toBeInTheDocument();
    // Nurses do not start consultations.
    expect(within(queue).queryByRole("button", { name: "Start consult" })).toBeNull();

    fireEvent.click(within(queue).getByRole("button", { name: "Mark absent" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith("/queue/long-called/skip", expect.objectContaining({ method: "POST" })),
    );
  });

  it("opens the case sheet when a doctor starts the consultation", async () => {
    vi.spyOn(staffAuth, "user", "get").mockReturnValue({
      id: "doctor-1",
      name: "Synthetic Doctor",
      email: "doctor@example.test",
      role: "doctor",
      hospital_id: "hospital-1",
    });
    const request = vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/alerts")) return response({ items: [] });
      if (path.endsWith("/start")) return response({ status: "in_consult" });
      return response({
        items: [item({ visit_id: "called-1", token: "MED-007", status: "called" })],
        departments,
        next_cursor: null,
        missed: [],
        call_grace_seconds: 180,
      });
    });

    renderBoard();
    const queue = await screen.findByRole("region", { name: "Queue patients" });
    fireEvent.click(await within(queue).findByRole("button", { name: "Start consult" }));
    expect(await screen.findByText("Case sheet open")).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith("/queue/called-1/start", expect.objectContaining({ method: "POST" }));
  });

  it("explains a refused action without locking the board", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async (path) => {
      if (path.startsWith("/alerts")) return response({ items: [] });
      if (path === "/queue/visit-1/call")
        return new Response(
          JSON.stringify({ code: "DOCTOR_BUSY", active_token: "MED-009", detail: "busy" }),
          { status: 409 },
        );
      return response({ items: [item({})], departments, next_cursor: null, missed: [] });
    });

    renderBoard();
    const queue = await screen.findByRole("region", { name: "Queue patients" });
    fireEvent.click(await within(queue).findByRole("button", { name: "Call patient" }));
    expect(
      await screen.findByText(
        "Finish with MED-009 first: start the consultation, mark them absent or put them back in the queue.",
      ),
    ).toHaveAttribute("role", "alert");
    expect(within(queue).getByRole("button", { name: "Call patient" })).toBeEnabled();
  });

  it("says which danger sign the patient tapped, not its code", async () => {
    vi.spyOn(staffAuth, "request").mockImplementation(async (path) =>
      path.startsWith("/alerts")
        ? response({
            items: [
              {
                id: "alert-1",
                visit_id: "visit-1",
                rule_id: "RF_DANGER_TILE",
                level: "urgent",
                evidence: "DS_CHEST",
                reason_en: "Patient selected a danger-sign help tile.",
                token_label: "MED-003",
                source: "tile",
              },
            ],
          })
        : response({ items: [item({})], departments, next_cursor: null, missed: [] }),
    );
    renderBoard();
    const alerts = await screen.findByRole("region", { name: "Unacknowledged staff alerts" });
    expect(alerts).toHaveTextContent("patient tapped “Chest discomfort”");
    expect(alerts).not.toHaveTextContent("DS_CHEST");
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

describe("Phase 5 live board connection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renews an expired sign-in before reconnecting the live board", async () => {
    const sockets: FakeSocket[] = [];
    class FakeSocket {
      onopen?: () => void;
      onmessage?: (event: { data: string }) => void;
      onclose?: (event: { code: number }) => void;
      onerror?: () => void;
      sent: string[] = [];
      constructor(readonly url: URL) {
        sockets.push(this);
      }
      send(data: string) {
        this.sent.push(data);
      }
      close() {}
    }
    vi.stubGlobal("WebSocket", FakeSocket);
    let token = "first-sign-in";
    vi.spyOn(staffAuth, "accessToken", "get").mockImplementation(() => token);
    const refresh = vi.spyOn(staffAuth, "refresh").mockImplementation(async () => {
      token = "renewed-sign-in";
      return null;
    });
    vi.spyOn(staffAuth, "request").mockImplementation(async (path) =>
      path.startsWith("/alerts")
        ? response({ items: [] })
        : response({ items: [], departments: [departments[0]], next_cursor: null }),
    );
    const boards = () => sockets.filter((socket) => !socket.url.searchParams.has("alerts_only"));

    renderBoard();
    await waitFor(() => expect(boards()).toHaveLength(1));
    const [first] = boards();
    first.onopen?.();
    expect(JSON.parse(first.sent[0])).toEqual({ type: "auth", token: "first-sign-in" });

    // The server closes the socket when the 10-minute sign-in runs out.
    first.onclose?.({ code: 4401 });
    await waitFor(() => expect(boards()).toHaveLength(2));
    expect(refresh).toHaveBeenCalledTimes(1);
    const renewed = boards()[1];
    renewed.onopen?.();
    expect(JSON.parse(renewed.sent[0])).toEqual({ type: "auth", token: "renewed-sign-in" });
  });
});
