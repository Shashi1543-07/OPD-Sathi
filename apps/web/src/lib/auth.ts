import { apiOrigin } from "./api";

export type StaffRole = "admin" | "doctor" | "nurse";

export type StaffUser = {
  id: string;
  name: string;
  email: string;
  role: StaffRole;
  hospital_id: string;
  mfa_enabled?: boolean;
};

/**
 * What a signed-in person must do before anything else: choose a new password (after a
 * reset or on a new account), or set up two-step sign-in (required for their role).
 */
export type Restriction = "password_change" | "mfa_setup";

export type AuthResponse = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  restrictions?: Restriction[];
  user: StaffUser;
};

/** The password was right; the session waits for a one-time code (or a recovery code). */
export type MfaChallenge = { mfaToken: string };

type LoginResponse = AuthResponse | { mfa_required: true; mfa_token: string };

type Problem = { code?: string; detail?: string };

const STAFF_GET_CACHE_MS = 8_000;
const getResponseCache = new Map<
  string,
  { expiresAt: number; response: Response }
>();
const getRequestFlights = new Map<string, Promise<Response>>();
let accessToken: string | null = null;
let currentUser: StaffUser | null = null;
let restrictions: Restriction[] = [];
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

async function parseAuth<T = AuthResponse>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as T | Problem;
  if (!response.ok) {
    const problem = payload as Problem;
    throw new Error(problem.code ?? "SIGN_IN_FAILED");
  }
  return payload as T;
}

/** Take the tokens of a new or renewed session and tell every listener. */
function adoptSession(auth: AuthResponse) {
  accessToken = auth.access_token;
  restrictions = auth.restrictions ?? [];
  rememberAccount(auth.user.id);
  publish(auth.user);
}

export const staffAuth = {
  get accessToken() {
    return accessToken;
  },
  get user() {
    return currentUser;
  },
  /** Steps the signed-in person must finish first; empty for a normal session. */
  get restrictions() {
    return restrictions;
  },
  /**
   * Use the session a server response carries: after a password change, or after turning
   * two-step sign-in on or off (each returns fresh tokens without the old restriction).
   */
  adopt(auth: AuthResponse) {
    adoptSession(auth);
  },
  subscribe(listener: (user: StaffUser | null) => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** Sign in; returns a challenge when the account uses two-step sign-in. */
  async login(hospitalId: string, email: string, password: string): Promise<MfaChallenge | null> {
    const auth = await parseAuth<LoginResponse>(
      await authRequest("/auth/login", {
        hospital_id: hospitalId,
        email,
        password,
      }),
    );
    if ("mfa_required" in auth) return { mfaToken: auth.mfa_token };
    adoptSession(auth);
    return null;
  },
  /** The second step: a code from the authenticator app, or one recovery code. */
  async verifyMfa(challenge: MfaChallenge, answer: { code: string } | { recoveryCode: string }) {
    const auth = await parseAuth(
      await authRequest("/auth/mfa/verify", {
        mfa_token: challenge.mfaToken,
        ...("code" in answer ? { code: answer.code } : { recovery_code: answer.recoveryCode }),
      }),
    );
    adoptSession(auth);
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
        adoptSession(auth);
        return auth.user;
      } catch (cause) {
        if (cause instanceof Error && ["UNAUTHENTICATED", "ACCOUNT_CHANGED"].includes(cause.message)) {
          forgetAccount(account);
        }
        accessToken = null;
        restrictions = [];
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
    restrictions = [];
    publish(null);
  },
  async logout() {
    const account = currentUser?.id ?? tabAccount();
    try {
      await authRequest("/auth/logout", account ? { account } : undefined);
    } finally {
      forgetAccount(account);
      accessToken = null;
      restrictions = [];
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
