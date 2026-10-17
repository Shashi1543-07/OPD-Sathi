/**
 * Kiosk voice helpers.
 *
 * Listening: record one short answer and send it to the server for transcription
 * (Sarvam or Gemini). If the server has no speech provider, use the browser's own
 * speech recognition (Chrome and Edge support en-IN, hi-IN and mr-IN). If neither is
 * available the kiosk falls back to touch and typing.
 *
 * Speaking: ask the server for natural speech; otherwise use the browser voice.
 */

export type ServerSpeechMode = "sarvam" | "gemini" | "browser";
export type SpeechCapabilities = {
  /** "off": the patient said no to online AI help, so there is no voice input at all. */
  stt: ServerSpeechMode | "off";
  tts: ServerSpeechMode;
  ai: boolean;
  /** The patient allowed online AI help (their own words may be sent to a voice service). */
  online?: boolean;
};

const locales: Record<string, string> = { en: "en-IN", hi: "hi-IN", mr: "mr-IN" };

type RecognitionResultEvent = {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
};
type BrowserRecognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  onresult: ((event: RecognitionResultEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type RecognitionConstructor = new () => BrowserRecognition;

function recognitionConstructor(): RecognitionConstructor | undefined {
  const scope = window as unknown as {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  };
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition;
}

export function browserRecognitionAvailable() {
  // Chrome defines the recogniser on any page but only lets a secure page (HTTPS or
  // localhost) use the microphone, so a phone on http://<LAN address> must tap or type.
  return window.isSecureContext !== false && Boolean(recognitionConstructor());
}

/** False on a plain-HTTP page (a phone on the hospital Wi-Fi): no microphone there. */
export function secureVoiceAvailable() {
  return window.isSecureContext !== false;
}

export function recordingAvailable() {
  return (
    typeof window.MediaRecorder !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

export type ListenHandlers = {
  onLevel?: (level: number) => void;
  onPartial?: (text: string) => void;
  /** Called once with the final text ("" if nothing was understood). */
  onFinal: (text: string) => void;
  onError: (code: "permission_denied" | "unsupported" | "failed") => void;
  /** Recording has stopped and the clip is being transcribed. */
  onProcessing?: () => void;
};

export type Listener = { stop: () => void; cancel: () => void };

/** Listen with the browser's own recogniser (needs Chrome or Edge, online). */
export function listenInBrowser(language: string, handlers: ListenHandlers): Listener {
  const Recognition = recognitionConstructor();
  if (!Recognition) {
    handlers.onError("unsupported");
    return { stop: () => undefined, cancel: () => undefined };
  }
  const recognition = new Recognition();
  recognition.lang = locales[language] ?? "en-IN";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;
  let finalText = "";
  let latest = "";
  let done = false;
  let cancelled = false;
  recognition.onresult = (event) => {
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      if (result.isFinal) finalText += result[0].transcript;
      else interim += result[0].transcript;
    }
    latest = `${finalText} ${interim}`.trim();
    handlers.onPartial?.(latest);
  };
  recognition.onerror = (event) => {
    if (done || cancelled) return;
    if (event.error === "no-speech" || event.error === "aborted") return;
    done = true;
    handlers.onError(event.error === "not-allowed" ? "permission_denied" : "failed");
  };
  recognition.onend = () => {
    if (done || cancelled) return;
    done = true;
    handlers.onFinal((finalText || latest).trim());
  };
  try {
    recognition.start();
  } catch {
    handlers.onError("failed");
  }
  return {
    stop: () => recognition.stop(),
    cancel: () => {
      cancelled = true;
      recognition.abort();
    },
  };
}

function preferredMimeType() {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return candidates.find((type) => MediaRecorder.isTypeSupported?.(type)) ?? "";
}

/**
 * Record one answer and hand the clip to ``transcribe``. Recording stops on tap, after
 * ~1.6 s of silence following speech, or after 25 s.
 */
export function listenWithServer(
  transcribe: (clip: Blob) => Promise<string>,
  handlers: ListenHandlers,
): Listener {
  let recorder: MediaRecorder | undefined;
  let stream: MediaStream | undefined;
  let audioContext: AudioContext | undefined;
  let frame = 0;
  let cancelled = false;
  let stopped = false;
  const chunks: Blob[] = [];

  const cleanup = () => {
    if (frame) cancelAnimationFrame(frame);
    stream?.getTracks().forEach((track) => track.stop());
    void audioContext?.close().catch(() => undefined);
    handlers.onLevel?.(0);
  };
  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    else cleanup();
  };

  void (async () => {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (cause) {
      const name = cause instanceof DOMException ? cause.name : "";
      handlers.onError(name === "NotAllowedError" ? "permission_denied" : "unsupported");
      return;
    }
    if (cancelled) {
      cleanup();
      return;
    }
    const mimeType = preferredMimeType();
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorder.ondataavailable = (event) => {
      if (event.data.size) chunks.push(event.data);
    };
    recorder.onstop = () => {
      cleanup();
      if (cancelled) return;
      const clip = new Blob(chunks, { type: recorder?.mimeType || mimeType || "audio/webm" });
      if (clip.size < 1200) {
        handlers.onFinal("");
        return;
      }
      handlers.onProcessing?.();
      transcribe(clip)
        .then((text) => handlers.onFinal(text.trim()))
        .catch(() => handlers.onError("failed"));
    };
    recorder.start(250);

    // Level meter and end-of-speech detection.
    audioContext = new AudioContext();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    audioContext.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    const started = performance.now();
    let heardSpeech = false;
    let lastSpeech = started;
    const tick = () => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const value of samples) sum += value * value;
      const level = Math.sqrt(sum / samples.length);
      handlers.onLevel?.(level);
      const now = performance.now();
      if (level > 0.02) {
        heardSpeech = true;
        lastSpeech = now;
      }
      const silentFor = now - lastSpeech;
      if (
        (heardSpeech && silentFor > 1600) ||
        (!heardSpeech && now - started > 8000) ||
        now - started > 25_000
      ) {
        stop();
        return;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  })();

  return {
    stop,
    cancel: () => {
      cancelled = true;
      stop();
      cleanup();
    },
  };
}

// ---------------------------------------------------------------------------
// Speaking

let currentAudio: HTMLAudioElement | undefined;
let currentUrl: string | undefined;
// Bumped by every stop, so a sentence queue from an earlier read stops speaking.
let speechRun = 0;

export function stopSpeaking() {
  speechRun += 1;
  currentAudio?.pause();
  currentAudio = undefined;
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = undefined;
  window.speechSynthesis?.cancel();
}

/**
 * Chrome stops a single utterance after about 15 seconds, cutting long text (the consent
 * notice, the read-back) mid-sentence. Read it sentence by sentence instead.
 */
export function sentences(text: string, maxLength = 180): string[] {
  const parts = text.match(/[^.?!।]+[.?!।]*\s*/g) ?? [text];
  const chunks: string[] = [];
  for (const part of parts.map((item) => item.trim()).filter(Boolean)) {
    if (part.length <= maxLength) {
      chunks.push(part);
      continue;
    }
    // A very long sentence: break at commas, then at spaces.
    let rest = part;
    while (rest.length > maxLength) {
      const window = rest.slice(0, maxLength);
      const cut = Math.max(window.lastIndexOf(","), window.lastIndexOf(" "));
      const at = cut > maxLength / 3 ? cut + 1 : maxLength;
      chunks.push(rest.slice(0, at).trim());
      rest = rest.slice(at).trim();
    }
    if (rest) chunks.push(rest);
  }
  return chunks;
}

function pickVoice(language: string, localOnly = false) {
  // Some browser voices are online services; a patient's own words go only to voices on
  // this device unless the patient allowed online AI help.
  const voices = (window.speechSynthesis?.getVoices() ?? []).filter(
    (voice) => !localOnly || voice.localService,
  );
  const locale = locales[language] ?? "en-IN";
  return (
    voices.find((voice) => voice.lang === locale) ??
    voices.find((voice) => voice.lang.startsWith(language)) ??
    // Marathi voices are rare; a Hindi voice reads Devanagari far better than English.
    (language === "mr" ? voices.find((voice) => voice.lang.startsWith("hi")) : undefined) ??
    (language === "en" ? voices.find((voice) => voice.lang.startsWith("en")) : undefined)
  );
}

export function browserVoiceAvailable(language: string) {
  return Boolean(window.speechSynthesis) && Boolean(pickVoice(language));
}

function speakSentence(text: string, language: string, localOnly: boolean): Promise<void> {
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text);
    const voice = pickVoice(language, localOnly);
    if (voice) utterance.voice = voice;
    utterance.lang = voice?.lang ?? locales[language] ?? "en-IN";
    utterance.rate = 0.92;
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    window.speechSynthesis.speak(utterance);
  });
}

async function speakInBrowser(text: string, language: string, localOnly = false): Promise<boolean> {
  if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) return false;
  // Without an on-device voice the text stays on screen rather than going online.
  if (localOnly && !pickVoice(language, true)) return false;
  window.speechSynthesis.cancel();
  const run = speechRun;
  for (const part of sentences(text)) {
    if (run !== speechRun) return true; // Stopped, or another read started.
    await speakSentence(part, language, localOnly);
  }
  return true;
}

/**
 * Play server audio if ``fetchAudio`` succeeds, otherwise use the browser voice. ``localOnly``
 * keeps the browser to voices on this device. Resolves to false when nothing could be read.
 */
export async function speak(
  text: string,
  language: string,
  fetchAudio?: () => Promise<Blob>,
  { localOnly = false }: { localOnly?: boolean } = {},
): Promise<boolean> {
  stopSpeaking();
  const run = speechRun;
  if (fetchAudio) {
    try {
      const blob = await fetchAudio();
      if (run !== speechRun) return true; // Stopped while the audio was loading.
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      currentAudio = audio;
      currentUrl = url;
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve();
        audio.onpause = () => resolve(); // Stopped by the patient.
        audio.onerror = () => resolve();
        audio.play().catch(() => resolve());
      });
      return true;
    } catch {
      // Fall through to the browser voice.
    }
  }
  return speakInBrowser(text, language, localOnly);
}

// Some browsers load voices asynchronously.
if (typeof window !== "undefined" && window.speechSynthesis) {
  window.speechSynthesis.getVoices();
}
