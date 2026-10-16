import { afterEach, describe, expect, it, vi } from "vitest";
import { newRequestId } from "./ids";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("newRequestId", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses the browser's randomUUID in a secure context", () => {
    const native = vi
      .spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValue("11111111-2222-4333-8444-555555555555");
    expect(newRequestId()).toBe("11111111-2222-4333-8444-555555555555");
    expect(native).toHaveBeenCalledOnce();
  });

  it("builds a valid, unique version-4 id where randomUUID is missing (plain-HTTP LAN page)", () => {
    // Over http://<LAN address> browsers leave randomUUID undefined; getRandomValues remains.
    const real = globalThis.crypto;
    vi.stubGlobal("crypto", {
      getRandomValues: <T extends ArrayBufferView>(array: T): T =>
        real.getRandomValues(array as unknown as Uint8Array) as unknown as T,
    });
    const ids = new Set(Array.from({ length: 200 }, () => newRequestId()));
    expect(ids.size).toBe(200);
    for (const id of ids) expect(id).toMatch(UUID_V4);
  });
});
