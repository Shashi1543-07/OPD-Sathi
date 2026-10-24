import { createHmac, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { request, type APIRequestContext } from "@playwright/test";

// What scripts/prod_smoke.py leaves for the scans and these specs: a synthetic hospital whose
// administrator has enrolled two-step sign-in.
export type StackState = {
  base: string;
  hospital_id: string;
  email: string;
  password: string;
  totp_secret: string;
};

export function stackState(): StackState {
  const file = process.env.STACK_STATE;
  if (!file) throw new Error("Set STACK_STATE to the state file scripts/prod_smoke.sh wrote.");
  return JSON.parse(readFileSync(file, "utf8")) as StackState;
}

function base32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/=+$/, "").toUpperCase()) {
    bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  }
  const bytes = bits.match(/.{8}/g) ?? [];
  return Buffer.from(bytes.map((byte) => parseInt(byte, 2)));
}

function totpAt(secret: string, step: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const digest = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const value = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return value.toString().padStart(6, "0");
}

// The API accepts each authenticator code once, so every sign-in waits for a code that no
// earlier sign-in (here or in the smoke test) can have used.
let lastStep = Math.floor(Date.now() / 30_000);
export async function freshCode(secret: string): Promise<string> {
  for (;;) {
    const step = Math.floor(Date.now() / 30_000);
    if (step > lastStep) {
      lastStep = step;
      return totpAt(secret, step);
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
}

export type Api = {
  get: (path: string) => Promise<Record<string, unknown>>;
  post: (path: string, body?: unknown) => Promise<Record<string, unknown>>;
  dispose: () => Promise<void>;
};

async function client(state: StackState, token?: string): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: `${state.base}/api/`,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: token ? { Authorization: `Bearer ${token}` } : {},
  });
}

function wrap(context: APIRequestContext): Api {
  const read = async (response: Awaited<ReturnType<APIRequestContext["get"]>>, what: string) => {
    if (!response.ok()) throw new Error(`${what}: ${response.status()} ${await response.text()}`);
    return (await response.json()) as Record<string, unknown>;
  };
  return {
    get: async (path) => read(await context.get(path), `GET ${path}`),
    post: async (path, body) =>
      read(
        await context.post(path, { data: body, headers: { "Idempotency-Key": randomUUID() } }),
        `POST ${path}`,
      ),
    dispose: () => context.dispose(),
  };
}

/** The synthetic administrator, signed in through the API with password and code. */
export async function adminApi(state: StackState): Promise<Api> {
  const anonymous = wrap(await client(state));
  const login = await anonymous.post("auth/login", {
    hospital_id: state.hospital_id,
    email: state.email,
    password: state.password,
  });
  const verified = await anonymous.post("auth/mfa/verify", {
    mfa_token: login.mfa_token,
    code: await freshCode(state.totp_secret),
  });
  await anonymous.dispose();
  return wrap(await client(state, String(verified.access_token)));
}
