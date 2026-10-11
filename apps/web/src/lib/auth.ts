import { apiOrigin } from "./api";

export type StaffRole = "admin" | "doctor" | "nurse";

export type StaffUser = {
  id: string;
  name: string;
  email: string;
  role: StaffRole;
  hospital_id: string;
};

type AuthResponse = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: StaffUser;
};

type Problem = { code?: string; detail?: string };

const STAFF_GET_CACHE_MS = 8_000;
const getResponseCache = new Map<
  string,
  { expiresAt: number; response: Response }
>();
const getRequestFlights = new Map<string, Promise<Response>>();
let accessToken: string | null = null;
let currentUser: StaffUser | null = null;
let refreshFlight: Promise<StaffUser | null> | null = null;
const listeners = new Set<(user: StaffUser | null) => void>();

// Each staff account has its own sign-in cookie, so a doctor and an administrator can
// work side by side in two tabs of one browser. A tab remembers its own account (tab
// storage); new tabs start with the account last signed in on this browser.
const TAB_ACCOUNT_KEY = "osa.staff.account";
const LAST_ACCOUNT_KEY = "osa.staff.lastAccount";
/** Marks a tab that chose "Use another account": it shows sign-in instead of the last account. */
const NO_ACCOUNT = "none";

function tabAccount(): string | null {
  try {
    const own = sessionStorage.getItem(TAB_ACCOUNT_KEY);
    if (own === NO_ACCOUNT) return null;
    return own ?? localStorage.getItem(LAST_ACCOUNT_KEY);
  } catch {
    return null;
  }
}

function tabWantsAnotherAccount(): boolean {
  try {
    return sessionStorage.getItem(TAB_ACCOUNT_KEY) === NO_ACCOUNT;
  } catch {
    return false;
  }
}

function rememberAccount(userId: string) {
  try {
    sessionStorage.setItem(TAB_ACCOUNT_KEY, userId);
    localStorage.setItem(LAST_ACCOUNT_KEY, userId);
  } catch {
    // Storage blocked: the tab still works until it is reloaded.
  }
}

function forgetAccount(userId: string | null) {
  try {
    sessionStorage.removeItem(TAB_ACCOUNT_KEY);
    if (userId && localStorage.getItem(LAST_ACCOUNT_KEY) === userId) {
      localStorage.removeItem(LAST_ACCOUNT_KEY);
    }
  } catch {
    // Nothing stored to forget.
  }
}

/**
 * Tabs of the same account take turns renewing. The server rotates the sign-in cookie on
 * every renewal and treats a reused one as stolen, so two tabs renewing at the same moment
 * (for example after the laptop wakes) used to sign everyone out.
 */
function oneTabAtATime<T>(name: string, work: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  return locks ? locks.request(name, work) : work();
}

function publish(user: StaffUser | null) {
  if (
    currentUser?.id !== user?.id ||
    currentUser?.hospital_id !== user?.hospital_id
  ) {
    getResponseCache.clear();
    getRequestFlights.clear();
  }
  currentUser = user;
  for (const listener of listeners) listener(user);
}

async function authRequest(path: string, body?: unknown): Promise<Response> {
  return fetch(`${apiOrigin}${path}`, {
    method: "POST",
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function parseAuth(response: Response): Promise<AuthResponse> {
  const payload = (await response.json()) as AuthResponse | Problem;
  if (!response.ok) {
    const problem = payload as Problem;
    throw new Error(problem.code ?? "SIGN_IN_FAILED");
  }
  return payload as AuthResponse;
}

export const staffAuth = {
  get accessToken() {
    return accessToken;
  },
  get user() {
    return currentUser;
  },
  subscribe(listener: (user: StaffUser | null) => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  async login(hospitalId: string, email: string, password: string) {
    const auth = await parseAuth(
      await authRequest("/auth/login", {
        hospital_id: hospitalId,
        email,
        password,
      }),
    );
    accessToken = auth.access_token;
    rememberAccount(auth.user.id);
    publish(auth.user);
  },
  refresh(): Promise<StaffUser | null> {
    if (refreshFlight) return refreshFlight;
    if (!currentUser && tabWantsAnotherAccount()) return Promise.resolve(null);
    const account = currentUser?.id ?? tabAccount();
    refreshFlight = (async () => {
      try {
        const auth = await oneTabAtATime(`osa-staff-refresh:${account ?? "default"}`, async () =>
          parseAuth(await authRequest("/auth/refresh", account ? { account } : undefined)),
        );
        // A tab never silently turns into someone else.
        if (currentUser && auth.user.id !== currentUser.id) throw new Error("ACCOUNT_CHANGED");
        accessToken = auth.access_token;
        rememberAccount(auth.user.id);
        publish(auth.user);
        return auth.user;
      } catch (cause) {
        if (cause instanceof Error && ["UNAUTHENTICATED", "ACCOUNT_CHANGED"].includes(cause.message)) {
          forgetAccount(account);
        }
        accessToken = null;
        publish(null);
        return null;
      } finally {
        refreshFlight = null;
      }
    })();
    return refreshFlight;
  },
  /**
   * Show the sign-in form in this tab only. Unlike sign out, the current account stays
   * signed in on its other tabs, so a second person can sign in alongside it.
   */
  switchAccount() {
    try {
      sessionStorage.setItem(TAB_ACCOUNT_KEY, NO_ACCOUNT);
    } catch {
      // Without tab storage the next reload simply returns to the last account.
    }
    accessToken = null;
    publish(null);
  },
  async logout() {
    const account = currentUser?.id ?? tabAccount();
    try {
      await authRequest("/auth/logout", account ? { account } : undefined);
    } finally {
      forgetAccount(account);
      accessToken = null;
      publish(null);
    }
  },
  async request(path: string, init: RequestInit = {}) {
    if (!accessToken && !(await this.refresh())) {
      return new Response(null, { status: 401 });
    }
    const send = () =>
      fetch(`${apiOrigin}${path}`, {
        ...init,
        headers: {
          ...init.headers,
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        credentials: "include",
      });
    const method = (init.method ?? "GET").toUpperCase();
    const isCacheableGet = method === "GET" && init.cache !== "no-store";
    const cacheKey = `${currentUser?.hospital_id ?? ""}:${currentUser?.id ?? ""}:${path}`;
    if (!isCacheableGet) {
      if (method !== "GET") getResponseCache.clear();
      let response = await send();
      if (response.status === 401 && (await this.refresh()))
        response = await send();
      return response;
    }
    const cached = getResponseCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) return cached.response.clone();
    getResponseCache.delete(cacheKey);
    const active = getRequestFlights.get(cacheKey);
    if (active) return (await active).clone();
    const flight = (async () => {
      let response = await send();
      if (response.status === 401 && (await this.refresh()))
        response = await send();
      if (response.ok) {
        getResponseCache.set(cacheKey, {
          expiresAt: Date.now() + STAFF_GET_CACHE_MS,
          response: response.clone(),
        });
      }
      return response;
    })();
    getRequestFlights.set(cacheKey, flight);
    try {
      return (await flight).clone();
    } finally {
      getRequestFlights.delete(cacheKey);
    }
  },
};
