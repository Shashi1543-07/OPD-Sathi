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

const apiOrigin = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
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
    publish(auth.user);
  },
  refresh(): Promise<StaffUser | null> {
    if (refreshFlight) return refreshFlight;
    refreshFlight = (async () => {
      try {
        const auth = await parseAuth(await authRequest("/auth/refresh"));
        accessToken = auth.access_token;
        publish(auth.user);
        return auth.user;
      } catch {
        accessToken = null;
        publish(null);
        return null;
      } finally {
        refreshFlight = null;
      }
    })();
    return refreshFlight;
  },
  async logout() {
    try {
      await authRequest("/auth/logout");
    } finally {
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
