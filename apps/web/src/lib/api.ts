/**
 * Where the API lives: "/api" on the same address as the web app, in the Docker build
 * (nginx) and in development (the Vite proxy alike), so phones on the clinic Wi-Fi reach it
 * through the one port they can see. VITE_API_URL can point elsewhere.
 */
export const apiOrigin = (import.meta.env.VITE_API_URL ?? "/api").replace(/\/$/, "");

/** A WebSocket URL for an API path such as "/ws/staff/queue", relative base or not. */
export function apiSocketUrl(path: string): URL {
  const url = new URL(`${apiOrigin}${path}`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url;
}
