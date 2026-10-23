"""Sign the smoke test's administrator in and print a fresh access token (for scripts/dast.sh).

Reads the sign-in details that scripts/prod_smoke.py saves when SMOKE_STATE_FILE is set: the
smoke stack's synthetic administrator, never a hospital's. Each call uses a new two-step code,
waiting for the next 30-second window when the current code was already used (the API refuses
a code twice).

Usage: python scripts/dast_token.py <state file>
"""

import json
import sys
import time

import httpx
import pyotp

state = json.load(open(sys.argv[1], encoding="utf-8"))
api = f"{state['base'].rstrip('/')}/api"
totp = pyotp.TOTP(state["totp_secret"])
# The smoke stack's certificate is self-signed.
client = httpx.Client(verify=False, timeout=30)  # noqa: S501 - test certificate

for _attempt in range(3):
    login = client.post(f"{api}/auth/login", json={
        "hospital_id": state["hospital_id"], "email": state["email"],
        "password": state["password"]})
    login.raise_for_status()
    verified = client.post(f"{api}/auth/mfa/verify", json={
        "mfa_token": login.json()["mfa_token"], "code": totp.now()})
    if verified.is_success:
        print(verified.json()["access_token"])
        sys.exit(0)
    time.sleep(31 - time.time() % 30)
sys.exit(f"could not sign in: {verified.status_code} {verified.text}")
