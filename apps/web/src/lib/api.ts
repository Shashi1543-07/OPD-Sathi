/**
 * Where the API lives. In development it is http://localhost:8000; in the Docker build
 * it is "/api" on the same address as the web app, so phones on the clinic Wi-Fi (the
 * token QR code) reach it through the one port they can see.
 */
export const apiOrigin = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

/** A WebSocket URL for an API path such as "/ws/staff/queue", relative base or not. */
export function apiSocketUrl(path: string): URL {
  const url = new URL(`${apiOrigin}${path}`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url;
}
