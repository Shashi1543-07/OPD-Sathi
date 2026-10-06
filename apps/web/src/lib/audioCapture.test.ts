import { afterEach, describe, expect, it, vi } from "vitest";
import { CaptureError, startMicrophoneCapture } from "./audioCapture";

describe("startMicrophoneCapture", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("falls back when browser audio APIs are unavailable", async () => {
    const socket = { readyState: WebSocket.OPEN } as WebSocket;
    await expect(startMicrophoneCapture(socket)).rejects.toMatchObject({
      code: "unsupported",
    } satisfies Partial<CaptureError>);
  });

  it("explains denied microphone permission and releases the socket", async () => {
    const send = vi.fn();
    const socket = { readyState: WebSocket.OPEN, send } as unknown as WebSocket;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi
          .fn()
          .mockRejectedValue(new DOMException("Permission denied", "NotAllowedError")),
      },
    });
    Object.defineProperty(window, "AudioWorkletNode", {
      configurable: true,
      value: class AudioWorkletNode {},
    });

    await expect(startMicrophoneCapture(socket)).rejects.toMatchObject({
      code: "permission_denied",
    } satisfies Partial<CaptureError>);
    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: "end" }));
  });
});
