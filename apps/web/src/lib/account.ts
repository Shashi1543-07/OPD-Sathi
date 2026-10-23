import { staffAuth } from "./auth";

type Problem = { code?: string; detail?: string; title?: string };

/** Call an account route; resolves with the JSON body or throws with the server's detail. */
export async function accountRequest<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
  const response = await staffAuth.request(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as Problem;
    throw new Error(problem.detail ?? problem.title ?? "");
  }
  return (response.status === 204 ? null : await response.json()) as T;
}
