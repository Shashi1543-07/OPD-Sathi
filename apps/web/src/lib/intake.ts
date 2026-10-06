import type { Language } from "../i18n";
import { staffAuth } from "./auth";

const apiOrigin = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const kioskKey = "osa.kiosk.access";
const patientKey = "osa.patient.session";

type Session = {
  visit_id: string;
  patient_access_token: string;
  consent?: ConsentCopy;
  stage?: string;
  assisted?: boolean;
};
export type ConsentCopy = {
  language: Language;
  version: string;
  sha256: string;
  text: string;
};
export type Question = {
  id: string;
  input: "text" | "scale";
  optional: boolean;
  prompt: string;
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
  case_sheet?: CaseSheet;
  readback?: string;
  answered?: (Question & { answer: string | number })[];
};

function getKioskToken() {
  return sessionStorage.getItem(kioskKey);
}

function getPatientSession(): Session | null {
  const stored = sessionStorage.getItem(patientKey);
  if (!stored) return null;
  try {
    return JSON.parse(stored) as Session;
  } catch {
    sessionStorage.removeItem(patientKey);
    return null;
  }
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
      detail?: string;
      title?: string;
    };
    throw new Error(
      problem.detail ??
        problem.title ??
        "The kiosk could not continue. Please ask staff for help.",
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
  const origin = apiOrigin.replace(/\/$/, "");
  const socketUrl =
    `${origin}/ws/intake/${encodeURIComponent(session.visit_id)}/audio`
      .replace(/^https:/, "wss:")
      .replace(/^http:/, "ws:");
  const socket = new WebSocket(socketUrl);
  socket.addEventListener("message", onMessage);
  socket.addEventListener("open", () => {
    socket.send(
      JSON.stringify({ type: "auth", token: session.patient_access_token }),
    );
  });
  return socket;
}

export const intake = {
  isKioskActivated: () => Boolean(getKioskToken()),
  activate: async (activationCode: string) => {
    const response = await call<{ access_token: string }>("/kiosks/activate", {
      method: "POST",
      body: JSON.stringify({ activation_code: activationCode }),
    });
    sessionStorage.setItem(kioskKey, response.access_token);
  },
  start: async (language: Language) => {
    const response = await call<Session & { consent: ConsentCopy }>(
      "/intake/sessions",
      { method: "POST", body: JSON.stringify({ language }) },
      getKioskToken() ?? undefined,
    );
    sessionStorage.setItem(patientKey, JSON.stringify(response));
    return response;
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
    sessionStorage.setItem(
      patientKey,
      JSON.stringify({ ...session, stage: "consent" }),
    );
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
      headers: { "Idempotency-Key": crypto.randomUUID() },
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
  connectAudio,
  confirm: () =>
    patientRequest<{
      confirmed: boolean;
      token: string;
      status_token: string;
      status_url: string;
      position: number | null;
    }>("/confirm", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),
  readbackAudio: async () => {
    const session = getPatientSession();
    if (!session) throw new Error("No active patient session");
    const response = await fetch(
      `${apiOrigin}/intake/sessions/${session.visit_id}/readback-audio`,
      { headers: { Authorization: `Bearer ${session.patient_access_token}` }, cache: "no-store" },
    );
    if (!response.ok) throw new Error("Read-back audio is unavailable");
    return response.blob();
  },
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
    sessionStorage.removeItem(patientKey);
  },
  finish: () => sessionStorage.removeItem(patientKey),
  setStage: (stage: string) => {
    const session = getPatientSession();
    if (session)
      sessionStorage.setItem(patientKey, JSON.stringify({ ...session, stage }));
  },
  session: getPatientSession,
};
