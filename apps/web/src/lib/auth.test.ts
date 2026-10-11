import { afterEach, expect, it, vi } from "vitest";
import { staffAuth } from "./auth";

const authResponse = {
  access_token: "synthetic-in-memory-token",
  token_type: "bearer",
  expires_in: 600,
  user: {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Synthetic Admin",
    email: "admin@example.test",
    role: "admin",
    hospital_id: "11111111-1111-4111-8111-111111111111",
  },
};

afterEach(async () => {
  await staffAuth.logout();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("shares one refresh request and keeps the access token in memory", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/auth/refresh")) {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return new Response(JSON.stringify(authResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", { status: 200, headers: init?.headers });
  });
  vi.stubGlobal("fetch", fetchMock);

  const [first, second] = await Promise.all([staffAuth.refresh(), staffAuth.refresh()]);
  expect(first?.email).toBe("admin@example.test");
  expect(second?.id).toBe(first?.id);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await staffAuth.request("/audit");
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls[1]?.[1]?.headers).toMatchObject({
    Authorization: "Bearer synthetic-in-memory-token",
  });
  // Only the (non-secret) account id is remembered, so each tab renews its own account;
  // the access token itself never touches browser storage.
  const stored = [window.localStorage, window.sessionStorage].flatMap((store) =>
    Array.from({ length: store.length }, (_, index) => store.getItem(store.key(index) ?? "")),
  );
  expect(stored.join(" ")).not.toContain("synthetic-in-memory-token");
  expect(window.localStorage.getItem("osa.staff.lastAccount")).toBe(authResponse.user.id);
});

it("renews the tab's own account and never turns a tab into another user", async () => {
  const bodies: unknown[] = [];
  let user = authResponse.user;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/auth/refresh")) {
      bodies.push(init?.body ? JSON.parse(String(init.body)) : null);
      return new Response(JSON.stringify({ ...authResponse, user }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(null, { status: 204 });
  });
  vi.stubGlobal("fetch", fetchMock);

  await staffAuth.refresh();
  expect(staffAuth.user?.id).toBe(authResponse.user.id);
  await staffAuth.refresh();
  expect(bodies.at(-1)).toEqual({ account: authResponse.user.id });

  // Someone else's session comes back: this tab signs out instead of switching.
  user = { ...authResponse.user, id: "33333333-3333-4333-8333-333333333333" };
  expect(await staffAuth.refresh()).toBeNull();
  expect(staffAuth.user).toBeNull();
});
