import { useSyncExternalStore } from "react";
import type { Language } from "../i18n";
import { apiOrigin, apiSocketUrl } from "./api";
import { staffAuth } from "./auth";
import { newRequestId } from "./ids";
import type { OfflineSubmission } from "./offlineOutbox";
import type { SpeechCapabilities } from "./voice";

const kioskKey = "osa.kiosk.access";
/** The long-lived device secret from activation; it renews the short-lived access token. */
const deviceKey = "osa.kiosk.device";
/** The name of the hospital this kiosk belongs to, shown on screen and on token slips. */
const hospitalKey = "osa.kiosk.hospital";
const patientKey = "osa.patient.session";
/** The last token issued on this phone, so reopening the check-in link shows it again. */
const ticketKey = "osa.mobile.ticket";
/** Renew the kiosk token this long before it expires, so a patient never meets an expired one. */
const RENEW_BEFORE_MS = 30 * 60_000;

type Session = {
  visit_id: string;
  patient_access_token: string;
  consent?: ConsentCopy;
  stage?: string;
  assisted?: boolean;
  /** Started from a hospital's phone check-in QR on the patient's own phone. */
  channel?: "mobile";
  /** The check-in code the phone session came from (its home page is /m/<code>). */
  checkin?: string;
  /** When the patient session expires (ms since epoch), so a stale one is never resumed. */
  expires_at?: number;
};

/** The OPD card a patient brings to the next visit: registration number and PIN. */
export type OpdCard = { number: string; pin: string };

/** A token issued on this phone: shown again instead of starting a second visit. */
export type MobileTicket = {
  token: string;
  statusUrl: string;
  departmentName: string | null;
  hospitalName: string | null;
  issuedAt: string;
  opdCard?: OpdCard | null;
};

/** The result of linking today's visit with an OPD card (the kiosk never sees history). */
export type CardLink = {
  linked: boolean;
  opd_number: string;
  visit_count: number;
  previous_visit: { id: string; date: string } | null;
};

export class IntakeRequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "IntakeRequestError";
  }
}
export type ConsentCopy = {
  language: Language;
  version: string;
  sha256: string;
  text: string;
};
export type AnswerOption = {
  id: string;
  label: string;
  none: boolean;
  /** Options that contradict this one; picking it clears them. */
  excludes?: string[];
};
export type Question = {
  id: string;
  input: "text" | "scale";
  optional: boolean;
  prompt: string;
  multi?: boolean;
  options?: AnswerOption[];
};
export type DangerTile = { id: string; label: string };
export type CaseSheet = {
  language: Language;
  chief_complaint: { text_original: string; text_en: string };
  hpi: {
    onset?: { text_original: string };
    severity_0_10?: number;
    associated_symptoms?: { text_original: string }[];
  };
  past_history: { conditions: string[] };
  medications: { name: string }[];
  allergies: { text_original: string }[];
  family_history: { text_original: string }[];
  red_flags: { id: string; level: string }[];
};
export type IntakeResponse = {
  complete?: boolean;
  question?: Question;
  progress?: { current: number; total: number };
  case_sheet?: CaseSheet;
  readback?: string;
  answered?: (Question & { answer: string | number })[];
  /** After "Back": the undone answer, shown pre-filled so the patient can change it. */
  draft?: string | number | null;
};

// Kiosk credentials survive tab restarts and reboots. The access token expires server-side
// (KIOSK_TOKEN_HOURS); the device secret renews it for as long as the kiosk stays in service.
function readKioskValue(key: string) {
  try {
    return localStorage.getItem(key) ?? sessionStorage.getItem(key);
  } catch {
    return sessionStorage.getItem(key);
  }
}

function writeKioskValue(key: string, value: string | null) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Fall back to the tab-scoped store below.
  }
  if (value) sessionStorage.setItem(key, value);
  else sessionStorage.removeItem(key);
}

const getKioskToken = () => readKioskValue(kioskKey);
const setKioskToken = (token: string | null) => writeKioskValue(kioskKey, token);
const getDeviceSecret = () => readKioskValue(deviceKey);

const hospitalListeners = new Set<() => void>();

function rememberHospital(name: string | null | undefined) {
  const next = name?.trim() || null;
  if (next === readKioskValue(hospitalKey)) return;
  writeKioskValue(hospitalKey, next);
  hospitalListeners.forEach((listener) => listener());
}

/** This kiosk's hospital name, or null before the kiosk is set up. */
export function useKioskHospitalName(): string | null {
  return useSyncExternalStore(
    (listener) => {
      hospitalListeners.add(listener);
      return () => hospitalListeners.delete(listener);
    },
    () => readKioskValue(hospitalKey),
  );
}

/** Forget this kiosk's pairing: it shows the activation screen until set up again. */
function forgetKiosk() {
  setKioskToken(null);
  writeKioskValue(deviceKey, null);
  rememberHospital(null);
}

function tokenExpiresAt(token: string): number | null {
  try {
    const payload = token.split(".")[1];
    const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as {
      exp?: number;
    };
    return typeof claims.exp === "number" ? claims.exp * 1000 : null;
  } catch {
    return null;
  }
}

let renewal: Promise<string | null> | null = null;

/**
 * A kiosk access token to use now, renewed with the device secret when it is missing or
 * close to expiry (or always, with `force`). The pairing is dropped only when the server
 * says this device is no longer paired (deleted, or set up on another device); network
 * errors and a turned-off kiosk keep it, so the kiosk recovers on its own.
 */
async function kioskToken(force = false): Promise<string | null> {
  const token = getKioskToken();
  const secret = getDeviceSecret();
  if (!secret) return token;
  const expiresAt = token ? tokenExpiresAt(token) : null;
  if (!force && token && expiresAt !== null && expiresAt - Date.now() > RENEW_BEFORE_MS) {
    return token;
  }
  renewal ??= call<{ access_token: string; hospital_name?: string }>("/kiosks/token", {
    method: "POST",
    body: JSON.stringify({ device_secret: secret }),
  })
    .then((response) => {
      setKioskToken(response.access_token);
      rememberHospital(response.hospital_name);
      return response.access_token;
    })
    .catch((cause: unknown) => {
      if (cause instanceof IntakeRequestError && cause.status === 401) {
        forgetKiosk();
        return null;
      }
      return getKioskToken();
    })
    .finally(() => {
      renewal = null;
    });
  return renewal;
}

/** A kiosk-authenticated request that renews the token and retries once if it was refused. */
async function kioskCall<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await kioskToken();
  try {
    return await call<T>(path, options, token ?? undefined);
  } catch (cause) {
    if (!(cause instanceof IntakeRequestError)) throw cause;
    if (cause.status === 401) {
      // Without a device secret (activated before renewal existed) there is no way back.
      if (!getDeviceSecret()) {
        setKioskToken(null);
        throw cause;
      }
      const renewed = await kioskToken(true);
      if (!renewed || renewed === token) throw cause;
      return call<T>(path, options, renewed);
    }
    // Refused while signed in: find out whether this device was replaced or the kiosk deleted.
    if (cause.code === "KIOSK_NOT_READY") await kioskToken(true);
    throw cause;
  }
}

function readSession(store: () => Storage): Session | null {
  try {
    const stored = store().getItem(patientKey);
    if (!stored) return null;
    try {
      return JSON.parse(stored) as Session;
    } catch {
      store().removeItem(patientKey);
      return null;
    }
  } catch {
    return null; // Storage blocked (private mode or site data switched off).
  }
}

/**
 * The patient session in progress. A shared kiosk keeps it for this tab only; a patient's own
 * phone keeps it across reloads and closed tabs until it expires, so a dropped connection or
 * an accidental refresh resumes the same visit instead of starting another one.
 */
function getPatientSession(): Session | null {
  const kiosk = readSession(() => sessionStorage);
  if (kiosk) return kiosk;
  const phone = readSession(() => localStorage);
  if (phone?.expires_at !== undefined && phone.expires_at <= Date.now()) {
    clearPatientSession();
    return null;
  }
  return phone;
}

function savePatientSession(session: Session) {
  try {
    (session.channel === "mobile" ? localStorage : sessionStorage).setItem(
      patientKey,
      JSON.stringify(session),
    );
  } catch {
    // Without storage the session lasts as long as this page; the server keeps the visit.
  }
}

function clearPatientSession() {
  for (const store of [() => sessionStorage, () => localStorage]) {
    try {
      store().removeItem(patientKey);
    } catch {
      // Storage blocked: nothing was saved there.
    }
  }
}

function todayKey(date: Date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

async function call<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const response = await fetch(`${apiOrigin}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as {
      code?: string;
      detail?: string;
      title?: string;
    };
    throw new IntakeRequestError(
      problem.code ?? "REQUEST_FAILED",
      problem.detail ??
        problem.title ??
        "The kiosk could not continue. Please ask staff for help.",
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function patientRequest<T>(path: string, options: RequestInit = {}) {
  const session = getPatientSession();
  if (!session)
    throw new Error("Please start again with help from the clinic staff.");
  return call<T>(
    `/intake/sessions/${session.visit_id}${path}`,
    options,
    session.patient_access_token,
  );
}

function connectAudio(onMessage: (message: MessageEvent<string>) => void) {
  const session = getPatientSession();
  if (!session) throw new Error("No active patient session");
  const socket = new WebSocket(
    apiSocketUrl(`/ws/intake/${encodeURIComponent(session.visit_id)}/audio`),
  );
  socket.addEventListener("message", onMessage);
  socket.addEventListener("open", () => {
    socket.send(
      JSON.stringify({ type: "auth", token: session.patient_access_token }),
    );
  });
  return socket;
}

export const intake = {
  isKioskActivated: () => Boolean(getKioskToken() || getDeviceSecret()),
  kioskAccessToken: getKioskToken,
  submitOffline: (submission: OfflineSubmission) =>
    kioskCall<{
      confirmed: boolean;
      token: string;
      status_token: string;
      status_url: string;
      position: number | null;
    }>("/intake/offline-submissions", {
      method: "POST",
      headers: { "Idempotency-Key": submission.idempotency_key },
      body: JSON.stringify(submission),
    }),
  activate: async (activationCode: string) => {
    const response = await call<{
      access_token: string;
      device_secret: string;
      hospital_name?: string;
    }>(
      "/kiosks/activate",
      {
        method: "POST",
        body: JSON.stringify({ activation_code: activationCode }),
      },
    );
    setKioskToken(response.access_token);
    writeKioskValue(deviceKey, response.device_secret);
    rememberHospital(response.hospital_name);
  },
  forgetKiosk,
  /** Tells the server this kiosk is switched on, so administrators can see it online. */
  heartbeat: async () => {
    const token = await kioskToken();
    if (!token) return;
    try {
      const beat = await call<{ active: boolean; hospital_name?: string }>(
        "/kiosks/heartbeat",
        { method: "POST" },
        token,
      );
      rememberHospital(beat.hospital_name);
    } catch {
      // Offline or briefly unreachable: the next beat will report it.
    }
  },
  start: async (language: Language) => {
    const response = await kioskCall<Session & { consent: ConsentCopy }>("/intake/sessions", {
      method: "POST",
      body: JSON.stringify({ language }),
    });
    savePatientSession(response);
    return response;
  },
  /** The hospital behind a phone check-in QR, or an error saying why it cannot start. */
  checkinDetails: (code: string) =>
    call<{ hospital_name: string; languages: Language[] }>(
      `/intake/checkin/${encodeURIComponent(code)}`,
    ),
  /** Start a visit on the patient's own phone from a hospital's check-in QR. */
  startMobile: async (code: string, language: Language) => {
    const response = await call<{
      visit_id: string;
      patient_access_token: string;
      consent: ConsentCopy;
      expires_in: number;
      hospital_name?: string;
    }>(`/intake/checkin/${encodeURIComponent(code)}/sessions`, {
      method: "POST",
      body: JSON.stringify({ language }),
    });
    const session: Session = {
      visit_id: response.visit_id,
      patient_access_token: response.patient_access_token,
      consent: response.consent,
      channel: "mobile",
      checkin: code,
      // A minute of margin so the phone never resumes a session the server just expired.
      expires_at: Date.now() + Math.max(0, response.expires_in - 60) * 1000,
    };
    savePatientSession(session);
    rememberHospital(response.hospital_name);
    return session;
  },
  /** Shown on screen and on the token on a phone, which has no kiosk pairing to name it. */
  rememberHospitalName: (name: string | null | undefined) => rememberHospital(name),
  rememberTicket: (ticket: MobileTicket) => {
    try {
      localStorage.setItem(ticketKey, JSON.stringify(ticket));
    } catch {
      // Without storage the patient keeps the token on screen and the status link.
    }
  },
  /** Today's token on this phone, if one was issued. */
  lastTicket: (): MobileTicket | null => {
    try {
      const stored = localStorage.getItem(ticketKey);
      if (!stored) return null;
      const ticket = JSON.parse(stored) as MobileTicket;
      if (todayKey(new Date(ticket.issuedAt)) !== todayKey(new Date())) {
        localStorage.removeItem(ticketKey);
        return null;
      }
      return ticket;
    } catch {
      return null;
    }
  },
  startAssisted: async (language: Language) => {
    const response = await staffAuth.request("/intake/assisted-sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language }),
    });
    if (!response.ok)
      throw new Error(
        "Could not start assisted registration. Please try again.",
      );
    const session = (await response.json()) as Session & { assisted: true };
    const consent = session.consent;
    savePatientSession({ ...session, stage: "consent" });
    return consent;
  },
  consent: (consent: ConsentCopy, granted: boolean) =>
    patientRequest<{ next: string; message?: string }>("/consent", {
      method: "POST",
      body: JSON.stringify({
        version: consent.version,
        sha256: consent.sha256,
        granted,
      }),
    }),
  identity: (
    age: number,
    sex: string,
    phone?: string,
    name?: string,
    smsOptIn = false,
  ) =>
    patientRequest<{ next: string }>("/identity", {
      method: "POST",
      body: JSON.stringify({
        age,
        sex,
        phone: phone || null,
        name: name || null,
        sms_opt_in: smsOptIn,
      }),
    }),
  dangerSigns: (tileIds: string[]) =>
    patientRequest<{ staff_help_required: boolean; patient_message?: string }>(
      "/danger-signs",
      {
        method: "POST",
        body: JSON.stringify({ tile_ids: tileIds }),
      },
    ),
  /** Join today's visit to the patient's earlier record with their OPD card. */
  linkOpdCard: (number: string, pin: string) =>
    patientRequest<CardLink>("/opd-card", {
      method: "POST",
      body: JSON.stringify({ number, pin }),
    }),
  /** Whether today's visit continues an earlier one (null: a new problem). */
  markFollowUp: (previousVisitId: string | null) =>
    patientRequest<{ previous_visit_id: string | null }>("/follow-up", {
      method: "POST",
      body: JSON.stringify({ previous_visit_id: previousVisitId }),
    }),
  dangerTiles: () => patientRequest<{ tiles: DangerTile[] }>("/danger-tiles"),
  nextQuestion: () =>
    patientRequest<IntakeResponse>("/interview/next-question"),
  answer: (
    questionId: string,
    answer: string | number | null,
    skip = false,
    inputMode: "touch" | "voice" = "touch",
  ) =>
    patientRequest<IntakeResponse>("/interview/answers", {
      method: "POST",
      headers: { "Idempotency-Key": newRequestId() },
      body: JSON.stringify({
        question_id: questionId,
        answer,
        skip,
        input_mode: inputMode,
      }),
    }),
  correct: (
    questionId: string,
    answer: string | number,
    inputMode: "touch" | "voice" = "touch",
  ) =>
    patientRequest<IntakeResponse>("/interview/answers", {
      method: "PATCH",
      body: JSON.stringify({
        question_id: questionId,
        answer,
        input_mode: inputMode,
      }),
    }),
  back: () =>
    patientRequest<IntakeResponse>("/interview/back", { method: "POST" }),
  connectAudio,
  confirm: () =>
    patientRequest<{
      confirmed: boolean;
      token: string;
      status_token: string;
      status_url: string;
      position: number | null;
      opd_card?: OpdCard | null;
    }>("/confirm", {
      method: "POST",
      headers: { "Idempotency-Key": newRequestId() },
    }),
  readbackAudio: async () => {
    const session = getPatientSession();
    if (!session) throw new Error("No active patient session");
    const response = await fetch(
      `${apiOrigin}/intake/sessions/${session.visit_id}/readback-audio`,
      {
        headers: { Authorization: `Bearer ${session.patient_access_token}` },
        cache: "no-store",
      },
    );
    if (!response.ok) throw new Error("Read-back audio is unavailable");
    return response.blob();
  },
  capabilities: () =>
    call<SpeechCapabilities>(
      "/intake/capabilities",
      {},
      getPatientSession()?.patient_access_token,
    ),
  transcribe: async (clip: Blob, questionId: string) => {
    const session = getPatientSession();
    if (!session) throw new Error("No active patient session");
    const response = await fetch(
      `${apiOrigin}/intake/sessions/${session.visit_id}/transcribe?question_id=${encodeURIComponent(questionId)}`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.patient_access_token}`,
          "Content-Type": clip.type || "audio/webm",
        },
        body: clip,
      },
    );
    if (!response.ok) throw new Error("Voice is unavailable");
    return ((await response.json()) as { text: string }).text;
  },
  speechAudio: async (
    kind: "question" | "consent" | "readback",
    questionId = "",
  ) => {
    const session = getPatientSession();
    if (!session) throw new Error("No active patient session");
    const response = await fetch(
      `${apiOrigin}/intake/sessions/${session.visit_id}/speak`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.patient_access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ kind, question_id: questionId }),
      },
    );
    if (!response.ok) throw new Error("Audio unavailable");
    return response.blob();
  },
  readPrescription: async (photo: Blob) => {
    const session = getPatientSession();
    if (!session) throw new Error("No active patient session");
    const response = await fetch(
      `${apiOrigin}/intake/sessions/${session.visit_id}/prescription-photo`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.patient_access_token}`,
          "Content-Type": photo.type || "image/jpeg",
        },
        body: photo,
      },
    );
    if (!response.ok) {
      // 413: too large; 415: not a photo; anything else: could not be read.
      const problem = (await response.json().catch(() => ({}))) as { code?: string };
      throw new IntakeRequestError(
        problem.code ?? "REQUEST_FAILED",
        "Prescription could not be read",
        response.status,
      );
    }
    return (
      (await response.json()) as {
        medicines: { name: string; dose: string; how_often: string }[];
      }
    ).medicines;
  },
  changeLanguage: async (language: Language) => {
    const result = await patientRequest<{ language: Language; consent: ConsentCopy }>(
      "/language",
      { method: "POST", body: JSON.stringify({ language }) },
    );
    const session = getPatientSession();
    if (session) savePatientSession({ ...session, consent: result.consent });
    return result;
  },
  requestHelp: () =>
    patientRequest<{ requested: boolean }>("/help", { method: "POST" }),
  needsAssistance: () =>
    patientRequest<{ needs_assistance: boolean }>("/needs-assistance", {
      method: "POST",
    }),
  cancel: async () => {
    const session = getPatientSession();
    if (session)
      await patientRequest<void>("", { method: "DELETE" }).catch(
        () => undefined,
      );
    clearPatientSession();
  },
  finish: clearPatientSession,
  setStage: (stage: string) => {
    const session = getPatientSession();
    if (session) savePatientSession({ ...session, stage });
  },
  session: getPatientSession,
};
