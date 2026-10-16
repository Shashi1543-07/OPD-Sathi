/**
 * A random RFC 4122 version-4 id, used for Idempotency-Key headers and offline submissions.
 *
 * `crypto.randomUUID` exists only in a secure context (HTTPS or localhost). Patients' phones
 * and second PCs open the app over plain http://<LAN address>, where it is undefined and every
 * call threw. `crypto.getRandomValues` is available on every origin, so build the id from it.
 */
export function newRequestId(): string {
  const source = globalThis.crypto;
  if (typeof source.randomUUID === "function") return source.randomUUID();
  const bytes = source.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
