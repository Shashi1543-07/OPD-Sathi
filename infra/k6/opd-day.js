// An OPD under load (Phase 7, tasks 7.8.2 and 7.8.3; docs/SCALING.md, "Load and soak tests").
//
// Run by scripts/load-test.sh against a production-mode stack prepared by
// scripts/load_prepare.py (synthetic data only). Four kinds of user at once:
//   checkins  patients checking in on the kiosks at ARRIVALS_PER_HOUR (default 300: a district
//             hospital's peak hour), answering each question after a pause, then confirming;
//   doctors   one per prepared doctor: call the next patient, start, consult, complete;
//   boards    staff screens refreshing the queue every 5 s;
//   status    patients' phones refreshing their status page every 15 s.
// Pass when every step's p95 is under 1.5 s, under 1% of requests fail, and every token is
// unique within its department (scripts/load-test.sh checks the database afterwards).

import http from "k6/http";
import { check, sleep, fail } from "k6";
import { Counter, Trend } from "k6/metrics";
import exec from "k6/execution";
import { SharedArray } from "k6/data";

const API = __ENV.API_URL || "http://api:8000";
const DURATION = __ENV.DURATION || "15m";
const ARRIVALS_PER_HOUR = Number(__ENV.ARRIVALS_PER_HOUR || 300);
const BOARDS = Number(__ENV.BOARDS || 20);
const STATUS_PAGES = Number(__ENV.STATUS_PAGES || 200);
// Seconds a patient takes per answer, and a doctor per consultation (scaled for short runs).
const ANSWER_PAUSE = Number(__ENV.ANSWER_PAUSE || 4);
const CONSULT_SECONDS = Number(__ENV.CONSULT_SECONDS || 120);

const fixtures = JSON.parse(open(__ENV.FIXTURES || "/load/fixtures.json"));
const kiosks = new SharedArray("kiosks", () => fixtures.kiosks);
const doctors = new SharedArray("doctors", () => fixtures.doctors);

const stepDuration = new Trend("opd_step_duration", true);
const checkinsCompleted = new Counter("opd_checkins_completed");
const tokensIssued = new Counter("opd_tokens_issued");
const patientsCalled = new Counter("opd_patients_called");

http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 404, 409));

export const options = {
  scenarios: {
    checkins: {
      executor: "constant-arrival-rate",
      exec: "checkIn",
      rate: ARRIVALS_PER_HOUR,
      timeUnit: "1h",
      duration: DURATION,
      preAllocatedVUs: 30,
      maxVUs: 200,
    },
    doctors: {
      executor: "per-vu-iterations",
      exec: "doctor",
      vus: doctors.length,
      iterations: 1,
      maxDuration: DURATION,
    },
    boards: {
      executor: "constant-vus",
      exec: "board",
      vus: BOARDS,
      duration: DURATION,
    },
    status: {
      executor: "constant-vus",
      exec: "statusPage",
      vus: STATUS_PAGES,
      duration: DURATION,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{step:checkin}": ["p(95)<1500"],
    "http_req_duration{step:call}": ["p(95)<1500"],
    "http_req_duration{step:board}": ["p(95)<1500"],
    "http_req_duration{step:status}": ["p(95)<1500"],
    opd_checkins_completed: ["count>0"],
  },
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

function headers(token, idempotent) {
  const result = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  if (idempotent) result["Idempotency-Key"] = `${exec.vu.idInTest}-${Date.now()}-${Math.random()}`;
  return result;
}

function post(path, token, body, step, idempotent = false) {
  const response = http.post(`${API}${path}`, body === null ? null : JSON.stringify(body), {
    headers: headers(token, idempotent),
    tags: { step },
  });
  stepDuration.add(response.timings.duration, { step });
  return response;
}

function get(path, token, step) {
  const response = http.get(`${API}${path}`, { headers: headers(token, false), tags: { step } });
  stepDuration.add(response.timings.duration, { step });
  return response;
}

// --- setup: patients already waiting, whose phones the status scenario refreshes ----------

function completeCheckIn(kioskToken, pause) {
  const started = post("/intake/sessions", kioskToken, { language: "en" }, "checkin");
  if (!check(started, { "session started": (r) => r.status === 201 })) return null;
  const body = started.json();
  const visit = `/intake/sessions/${body.visit_id}`;
  const patient = body.patient_access_token;
  post(`${visit}/consent`, patient, {
    version: body.consent.version, sha256: body.consent.sha256, granted: true,
  }, "checkin");
  post(`${visit}/identity`, patient, { age: 20 + (exec.vu.idInTest % 60), sex: "female", phone: null }, "checkin");
  post(`${visit}/danger-signs`, patient, { tile_ids: [] }, "checkin");
  let step = get(`${visit}/interview/next-question`, patient, "checkin").json();
  for (let i = 0; i < 40 && step && !step.complete; i += 1) {
    const question = step.question;
    let answer;
    if (question.id === "chief_concern") answer = { question_id: question.id, answer: "cough for a week" };
    else if (question.optional) answer = { question_id: question.id, skip: true };
    else answer = { question_id: question.id, answer: question.input === "scale" ? 3 : "not sure" };
    if (pause) sleep(pause * (0.5 + Math.random()));
    const answered = post(`${visit}/interview/answers`, patient, answer, "checkin", true);
    step = answered.status === 200 ? answered.json() : null;
  }
  const receipt = post(`${visit}/confirm`, patient, null, "checkin");
  if (!check(receipt, { "patient got a token": (r) => r.status === 200 && !!r.json("token") })) return null;
  return receipt.json();
}

export function setup() {
  const statusTokens = [];
  const waiting = Math.min(Number(__ENV.SETUP_PATIENTS || 60), 200);
  for (let i = 0; i < waiting; i += 1) {
    const receipt = completeCheckIn(kiosks[i % kiosks.length], 0);
    if (receipt) statusTokens.push(receipt.status_token);
  }
  if (!statusTokens.length) fail("no patient could check in during setup");
  return { statusTokens };
}

// --- scenarios ------------------------------------------------------------------------------

export function checkIn() {
  const receipt = completeCheckIn(kiosks[exec.scenario.iterationInTest % kiosks.length], ANSWER_PAUSE);
  if (receipt) {
    checkinsCompleted.add(1);
    tokensIssued.add(1, { department: receipt.department_code || "unrouted", token: receipt.token });
  }
}

function signIn(account) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const response = http.post(`${API}/auth/login`, JSON.stringify({
      hospital_id: fixtures.hospital_id, email: account.email, password: account.password,
    }), { headers: { "Content-Type": "application/json" }, tags: { step: "signin" } });
    if (response.status === 200) return response.json("access_token");
    sleep(15); // sign-in is limited to 10 a minute per address
  }
  fail(`could not sign in ${account.email}`);
  return null;
}

export function doctor() {
  // One iteration per VU: the scenario's iteration number picks the doctor (VU ids are
  // numbered across all scenarios, so they are not 1..n here).
  const index = exec.scenario.iterationInTest;
  const account = doctors[index];
  // Staggered start: sign-in is rate-limited per address.
  sleep(7 * index);
  let token = signIn(account);
  let signedInAt = Date.now();
  const deadline = Date.now() + parseDuration(DURATION) * 1000 - 30000;
  while (Date.now() < deadline) {
    if (Date.now() - signedInAt > 8 * 60 * 1000) {
      token = signIn(account); // access tokens last 10 minutes
      signedInAt = Date.now();
    }
    const called = post(`/queue/call-next?department_id=${account.department_id}`, token, null, "call", true);
    if (called.status !== 200) {
      sleep(15); // nobody waiting in this department yet
      continue;
    }
    patientsCalled.add(1);
    const visit = called.json("visit_id");
    post(`/queue/${visit}/start`, token, null, "call", true);
    sleep(CONSULT_SECONDS * (0.5 + Math.random()));
    post(`/queue/${visit}/complete`, token, null, "call", true);
  }
}

export function board() {
  const account = doctors[(exec.vu.idInTest - 1) % doctors.length];
  if (!__VU_TOKEN.value || Date.now() - __VU_TOKEN.at > 8 * 60 * 1000) {
    // Boards start after the doctors have signed in, spread out (rate limit per address).
    if (!__VU_TOKEN.value) sleep(7 * doctors.length + 7 * (exec.vu.idInTest % BOARDS));
    __VU_TOKEN.value = signIn(account);
    __VU_TOKEN.at = Date.now();
  }
  const response = get(`/queue?department_id=${account.department_id}`, __VU_TOKEN.value, "board");
  check(response, { "board loads": (r) => r.status === 200 });
  sleep(5);
}
const __VU_TOKEN = { value: null, at: 0 };

export function statusPage(data) {
  const token = data.statusTokens[exec.vu.idInTest % data.statusTokens.length];
  const response = http.get(`${API}/status/${token}`, { tags: { step: "status" } });
  stepDuration.add(response.timings.duration, { step: "status" });
  check(response, { "status loads": (r) => r.status === 200 });
  sleep(15);
}

function parseDuration(text) {
  const match = /^(\d+)([smh])$/.exec(text);
  if (!match) return 900;
  return Number(match[1]) * { s: 1, m: 60, h: 3600 }[match[2]];
}
