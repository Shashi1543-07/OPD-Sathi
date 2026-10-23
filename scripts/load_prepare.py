"""Prepare a running production-mode stack for the load test (Phase 7, task 7.8.2).

Signs in as the smoke test's synthetic administrator (scripts/prod_smoke.py writes its details
when SMOKE_STATE_FILE is set), pairs kiosks, and creates doctors who have chosen their own
passwords, one or more per department. Writes the fixtures infra/k6/opd-day.js reads.
Synthetic data only; never run against a hospital's installation.

Usage: python scripts/load_prepare.py <state file> <fixtures out> [kiosks] [doctors]
"""

import json
import sys
import time
import uuid
from typing import Any

import httpx
import pyotp

state = json.load(open(sys.argv[1], encoding="utf-8"))
OUT = sys.argv[2]
KIOSKS = int(sys.argv[3]) if len(sys.argv) > 3 else 10
DOCTORS = int(sys.argv[4]) if len(sys.argv) > 4 else 8
API = f"{state['base'].rstrip('/')}/api"
# The smoke stack's certificate is self-signed.
client = httpx.Client(verify=False, timeout=30)  # noqa: S501 - test certificate
# Activation and sign-in are rate-limited per address (10 a minute): pace them.
PACE_SECONDS = 7


def admin_token() -> str:
    for _ in range(3):
        login = client.post(f"{API}/auth/login", json={
            "hospital_id": state["hospital_id"], "email": state["email"],
            "password": state["password"]})
        login.raise_for_status()
        verified = client.post(f"{API}/auth/mfa/verify", json={
            "mfa_token": login.json()["mfa_token"],
            "code": pyotp.TOTP(state["totp_secret"]).now()})
        if verified.is_success:
            return str(verified.json()["access_token"])
        time.sleep(31 - time.time() % 30)
    raise SystemExit(f"administrator sign-in failed: {verified.text}")


def call(method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": uuid.uuid4().hex}
    response = client.request(method, f"{API}{path}", headers=headers, **kwargs)
    response.raise_for_status()
    return response


admin = admin_token()
departments = {
    item["code"]: item["id"]
    for item in call("GET", "/admin/departments?limit=100", admin).json()["items"]
}
codes = sorted(departments)

kiosk_tokens = []
for number in range(KIOSKS):
    kiosk = call("POST", "/admin/kiosks", admin, json={"label": f"Load kiosk {number + 1}"})
    paired = client.post(f"{API}/kiosks/activate",
                         json={"activation_code": kiosk.json()["activation_code"]})
    paired.raise_for_status()
    kiosk_tokens.append(paired.json()["access_token"])
    time.sleep(PACE_SECONDS)

doctors = []
for number in range(DOCTORS):
    code = codes[number % len(codes)]
    email = f"load-doctor-{number + 1}-{uuid.uuid4().hex[:6]}@example.test"
    first, final = "violet harbour thunder 7731", f"copper meadow lantern {4400 + number}"
    call("POST", "/admin/users", admin, json={
        "role": "doctor", "name": f"Load Doctor {number + 1}", "email": email,
        "password": first, "department_id": departments[code]})
    login = client.post(f"{API}/auth/login", json={
        "hospital_id": state["hospital_id"], "email": email, "password": first})
    login.raise_for_status()
    call("POST", "/auth/password", login.json()["access_token"],
         json={"current_password": first, "new_password": final})
    doctors.append({"email": email, "password": final, "department_id": departments[code]})
    time.sleep(PACE_SECONDS)

with open(OUT, "w", encoding="utf-8") as fixtures:
    json.dump({"hospital_id": state["hospital_id"], "kiosks": kiosk_tokens,
               "doctors": doctors, "departments": departments}, fixtures)
print(f"Prepared {len(kiosk_tokens)} kiosks and {len(doctors)} doctors.")
