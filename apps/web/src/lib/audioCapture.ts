/** Browser microphone capture as 16 kHz mono PCM16 WebSocket frames. */

export type CaptureStopReason = "manual" | "silence" | "limit";

export type CaptureCallbacks = {
  onLevel?: (level: number) => void;
  onStop?: (reason: CaptureStopReason) => void;
  stream?: MediaStream;
};

export type StopCapture = () => Promise<void>;

export type CaptureErrorCode =
  | "connection_not_ready"
  | "unsupported"
  | "permission_denied"
  | "startup_failed";

export class CaptureError extends Error {
  constructor(readonly code: CaptureErrorCode) {
    super(code);
    this.name = "CaptureError";
  }
}

const MAX_CAPTURE_MS = 60_000;

const WORKLET_SOURCE = `
class Pcm16Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.frame = [];
    this.silentSeconds = 0;
    this.ended = false;
  }
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input || this.ended) return true;
    let sum = 0;
    const step = 16000 / sampleRate;
    for (let i = 0; i < input.length; i += 1) {
      const value = input[i];
      sum += value * value;
      this.phase += step;
      if (this.phase >= 1) {
        this.phase -= 1;
        this.frame.push(value);
        if (this.frame.length === 1600) {
          const pcm = new Int16Array(1600);
          for (let j = 0; j < 1600; j += 1) {
            const sample = Math.max(-1, Math.min(1, this.frame[j]));
            pcm[j] = sample < 0 ? sample * 32768 : sample * 32767;
          }
          this.port.postMessage({ type: 'frame', data: pcm.buffer }, [pcm.buffer]);
          this.frame = [];
        }
      }
    }
    const level = Math.sqrt(sum / input.length);
    this.port.postMessage({ type: 'level', value: level });
    this.silentSeconds = level < 0.012 ? this.silentSeconds + input.length / sampleRate : 0;
    if (this.silentSeconds >= 1.2) {
      this.ended = true;
      this.port.postMessage({ type: 'silence' });
    }
    return true;
  }
}
registerProcessor('osa-pcm16-capture', Pcm16Capture);
`;

export async function startMicrophoneCapture(
  socket: WebSocket,
  callbacks: CaptureCallbacks = {},
): Promise<StopCapture> {
  if (socket.readyState !== WebSocket.OPEN) {
    throw new CaptureError("connection_not_ready");
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
    throw new CaptureError("unsupported");
  }

  let stream: MediaStream | undefined;
  let context: AudioContext | undefined;
  let node: AudioWorkletNode | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let gain: GainNode | undefined;
  let finished = false;
  let timer: number | undefined;

  const cleanup = async (reason: CaptureStopReason) => {
    if (finished) return;
    finished = true;
    if (timer !== undefined) window.clearTimeout(timer);
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "end" }));
    }
    node?.port.close();
    node?.disconnect();
    source?.disconnect();
    gain?.disconnect();
    stream?.getTracks().forEach((track) => track.stop());
    if (context && context.state !== "closed") await context.close();
    callbacks.onStop?.(reason);
  };

  try {
    stream =
      callbacks.stream ??
      (await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      }));
    context = new AudioContext();
    const moduleUrl = URL.createObjectURL(
      new Blob([WORKLET_SOURCE], { type: "text/javascript" }),
    );
    try {
      await context.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }
    source = context.createMediaStreamSource(stream);
    node = new AudioWorkletNode(context, "osa-pcm16-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    gain = context.createGain();
    gain.gain.value = 0;
    node.port.onmessage = (message: MessageEvent) => {
      const data = message.data as {
        type?: string;
        value?: number;
        data?: ArrayBuffer;
      };
      if (
        data.type === "frame" &&
        data.data &&
        socket.readyState === WebSocket.OPEN
      ) {
        socket.send(data.data);
      } else if (data.type === "level") {
        callbacks.onLevel?.(data.value ?? 0);
      } else if (data.type === "silence") {
        void cleanup("silence");
      }
    };
    source.connect(node);
    node.connect(gain);
    gain.connect(context.destination);
    await context.resume();
    timer = window.setTimeout(() => void cleanup("limit"), MAX_CAPTURE_MS);
    return () => cleanup("manual");
  } catch (cause) {
    await cleanup("manual");
    if (cause instanceof DOMException && cause.name === "NotAllowedError") {
      throw new CaptureError("permission_denied");
    }
    throw new CaptureError("startup_failed");
  }
}
