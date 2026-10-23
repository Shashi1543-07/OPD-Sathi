"""Production smoke test: drive the production-mode stack through its public HTTPS address.

Phase 7, task 7.1.2. ``scripts/prod_smoke.sh`` starts the stack with docker-compose.prod.yml
and generated test secrets, then runs this. Synthetic data only. Checks, in order:

1. HTTPS answers and HTTP redirects to it; HSTS and the CSP are sent; the API docs are absent.
2. The administrator must enrol two-step sign-in, then can work.
3. A kiosk is registered, activated, and completes one patient check-in.
4. A new doctor must replace the administrator-set password, then calls the patient.

Usage: python scripts/prod_smoke.py https://localhost:8443 <admin email> <admin password>
       (the HTTP port is taken from SMOKE_HTTP_URL, default http://localhost:8080)
"""

import json
import os
import sys
import uuid
from typing import Any

import httpx
import pyotp

BASE, ADMIN_EMAIL, ADMIN_PASSWORD = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
API = f"{BASE}/api"
HTTP = os.getenv("SMOKE_HTTP_URL", "http://localhost:8080")
# The certificate is self-signed for the smoke test only; hospital installs use a real one.
client = httpx.Client(verify=False, timeout=30)  # noqa: S501 - test certificate
failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def call(method: str, path: str, token: str | None = None, **kwargs: Any) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if method != "GET":
        headers.setdefault("Idempotency-Key", uuid.uuid4().hex)
    return client.request(method, f"{API}{path}", headers=headers, **kwargs)


# 1. The edge.
ready = client.get(f"{API}/readyz")
check(ready.status_code == 200 and ready.json().get("status") == "ready", "API ready over HTTPS")
redirect = httpx.get(f"{HTTP}/staff", follow_redirects=False)
check(redirect.status_code == 301 and redirect.headers["location"].startswith("https://"),
      "HTTP redirects to HTTPS")
page = client.get(f"{BASE}/staff")
check("max-age=63072000" in page.headers.get("strict-transport-security", ""), "HSTS on pages")
csp = page.headers.get("content-security-policy", "")
check("script-src 'self'" in csp and "img-src 'self' data: blob:" in csp, "strict CSP on pages")
check(page.headers.get("cross-origin-embedder-policy") == "require-corp"
      and page.headers.get("cross-origin-opener-policy") == "same-origin",
      "page cross-origin isolated")
check(page.headers.get("server", "").lower() == "nginx", "server version hidden")
check(client.get(f"{API}/docs").status_code == 404, "API docs absent in production")
check(client.get(f"{API}/openapi.json").status_code == 404, "OpenAPI schema absent in production")
check(client.get(f"{API}/metrics").status_code == 404, "metrics not served to the network")
check("development-only" not in client.get(f"{BASE}/").text, "no development value in the page")

# 2. The administrator: password, then two-step enrolment before anything else.
hospitals = client.get(f"{API}/auth/hospitals").json()["items"]
hospital_id = hospitals[0]["id"]
login = call("POST", "/auth/login", json={
    "hospital_id": hospital_id, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
check(login.status_code == 200 and login.json().get("restrictions") == ["mfa_setup"],
      "administrator must enrol two-step sign-in")
admin = login.json()["access_token"]
check(call("GET", "/admin/users", admin).status_code == 403, "nothing opens before enrolment")
secret = call("POST", "/auth/mfa/setup", admin).json()["secret"]
enabled = call("POST", "/auth/mfa/enable", admin, json={"code": pyotp.TOTP(secret).now()})
check(enabled.status_code == 200 and len(enabled.json()["recovery_codes"]) == 10,
      "two-step sign-in enrolled with ten recovery codes")
admin = enabled.json()["access_token"]
if os.getenv("SMOKE_STATE_FILE"):
    # The dynamic scans (scripts/dast.sh) sign in as this synthetic administrator.
    with open(os.environ["SMOKE_STATE_FILE"], "w", encoding="utf-8") as state:
        json.dump({"base": BASE, "hospital_id": hospital_id, "email": ADMIN_EMAIL,
                   "password": ADMIN_PASSWORD, "totp_secret": secret}, state)

# 3. A kiosk checks a patient in.
departments = call("GET", "/admin/departments?limit=100", admin).json()["items"]
genmed = next(item for item in departments if item["code"] == "GENMED")
kiosk = call("POST", "/admin/kiosks", admin, json={"label": "Smoke kiosk"})
check(kiosk.status_code == 201, "kiosk registered")
paired = call("POST", "/kiosks/activate", json={"activation_code": kiosk.json()["activation_code"]})
check(paired.status_code == 200, "kiosk activated")
session = call("POST", "/intake/sessions", paired.json()["access_token"], json={"language": "en"})
body = session.json()
visit, patient = body["visit_id"], body["patient_access_token"]


def intake(path: str, payload: object, idempotent: bool = False) -> httpx.Response:
    headers = {"Idempotency-Key": uuid.uuid4().hex} if idempotent else {}
    return client.post(f"{API}/intake/sessions/{visit}{path}", json=payload,
                       headers={"Authorization": f"Bearer {patient}", **headers})


consent = body["consent"]
intake("/consent", {"version": consent["version"], "sha256": consent["sha256"], "granted": True})
intake("/identity", {"age": 36, "sex": "female", "phone": None})
intake("/danger-signs", {"tile_ids": []})
step = client.get(f"{API}/intake/sessions/{visit}/interview/next-question",
                  headers={"Authorization": f"Bearer {patient}"}).json()
for _ in range(40):
    if step.get("complete"):
        break
    question = step["question"]
    if question["id"] == "chief_concern":
        answer: dict[str, Any] = {"question_id": "chief_concern", "answer": "cough for a week"}
    elif question["optional"]:
        answer = {"question_id": question["id"], "skip": True}
    else:
        answer = {"question_id": question["id"],
                  "answer": 3 if question["input"] == "scale" else "not sure"}
    step = intake("/interview/answers", answer, idempotent=True).json()
receipt = intake("/confirm", None)
check(receipt.status_code == 200 and bool(receipt.json().get("token")), "patient got a token")

# 4. A new doctor replaces the administrator-set password, then calls the patient.
doctor_email = f"smoke-doctor-{uuid.uuid4().hex[:8]}@example.test"
created = call("POST", "/admin/users", admin, json={
    "role": "doctor", "name": "Smoke Doctor", "email": doctor_email,
    "password": "violet harbour thunder 7731", "department_id": genmed["id"]})
check(created.status_code == 201, "doctor account created")
doctor_login = call("POST", "/auth/login", json={
    "hospital_id": hospital_id, "email": doctor_email, "password": "violet harbour thunder 7731"})
check(doctor_login.json().get("restrictions") == ["password_change"],
      "new doctor must choose their own password")
changed = call("POST", "/auth/password", doctor_login.json()["access_token"], json={
    "current_password": "violet harbour thunder 7731",
    "new_password": "copper meadow lantern 4418"})
check(changed.status_code == 200 and changed.json()["restrictions"] == [], "password changed")
called = call("POST", f"/queue/call-next?department_id={genmed['id']}",
              changed.json()["access_token"])
check(called.status_code == 200, "doctor called the next patient")

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nProduction smoke test passed.")
