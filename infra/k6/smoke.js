import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = __ENV.BASE_URL || "http://localhost:8000";

export const options = {
  stages: [
    { duration: "20s", target: 25 },
    { duration: "40s", target: 25 },
    { duration: "10s", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<200"],
  },
};

export default function () {
  const response = http.get(`${baseUrl}/healthz`, { tags: { endpoint: "healthz" } });
  check(response, { "health check returns 200": (result) => result.status === 200 });
  sleep(1);
}
