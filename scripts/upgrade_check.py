"""Upgrade test (Phase 7, tasks 7.13.3 and 7.7.9): data and kiosks survive an upgrade.

Run ``before`` against a fresh installation of the previous release: it enrols the first
administrator's two-step sign-in, pairs a kiosk, checks a patient in and saves what it needs.
Upgrade the installation (``scripts/setup-hospital.sh`` again, at the new version), then run
``after``: the version is the new one, the visit and its case sheet are unchanged, the kiosk
still renews its token with the secret it was given before the upgrade and checks a patient
in, and routes added since the previous release answer for the old visit.

Usage:
  python scripts/upgrade_check.py before <base url> <admin email> <admin password> <state>
  python scripts/upgrade_check.py after <state> <expected version>

Synthetic data only; for the CI machine's own throwaway installation.
"""

import json
import sys
import time
import uuid
from typing import Any

import httpx
import pyotp

client = httpx.Client(verify=False, timeout=30)  # noqa: S501 - staging certificate
failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def call(api: str, method: str, path: str, token: str | None = None, **kwargs: Any) -> Any:
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if method != "GET":
        headers.setdefault("Idempotency-Key", uuid.uuid4().hex)
    return client.request(method, f"{api}{path}", headers=headers, **kwargs)


def check_in(api: str, kiosk_token: str, complaint: str) -> dict[str, Any]:
    """One patient through the kiosk, skipping what is optional; returns the receipt."""
    body = call(api, "POST", "/intake/sessions", kiosk_token, json={"language": "en"}).json()
    visit, patient = body["visit_id"], body["patient_access_token"]

    def intake(path: str, payload: object, idempotent: bool = False) -> Any:
        headers = {"Idempotency-Key": uuid.uuid4().hex} if idempotent else {}
        return client.post(f"{api}/intake/sessions/{visit}{path}", json=payload,
                           headers={"Authorization": f"Bearer {patient}", **headers})

    consent = body["consent"]
    intake("/consent", {"version": consent["version"], "sha256": consent["sha256"],
                        "granted": True})
    intake("/identity", {"age": 41, "sex": "female", "phone": None})
    intake("/danger-signs", {"tile_ids": []})
    step = client.get(f"{api}/intake/sessions/{visit}/interview/next-question",
                      headers={"Authorization": f"Bearer {patient}"}).json()
    for _ in range(40):
        if step.get("complete"):
            break
        question = step["question"]
        if question["id"] == "chief_concern":
            answer: dict[str, Any] = {"question_id": "chief_concern", "answer": complaint}
        elif question["optional"]:
            answer = {"question_id": question["id"], "skip": True}
        else:
            answer = {"question_id": question["id"],
                      "answer": 3 if question["input"] == "scale" else "not sure"}
        step = intake("/interview/answers", answer, idempotent=True).json()
    receipt = intake("/confirm", None)
    # The receipt has a "status" of its own (the visit's), so the HTTP status gets its own key.
    return {**receipt.json(), "visit_id": visit, "http_status": receipt.status_code}


def admin_sign_in(api: str, state: dict[str, Any]) -> str:
    login = call(api, "POST", "/auth/login", json={
        "hospital_id": state["hospital_id"], "email": state["email"],
        "password": state["password"]}).json()
    totp = pyotp.TOTP(state["totp_secret"])
    verified = call(api, "POST", "/auth/mfa/verify", json={
        "mfa_token": login["mfa_token"], "code": totp.now()})
    if verified.status_code == 401:
        # Each code works once; the one used at enrolment may still be the current one.
        time.sleep(totp.interval - time.time() % totp.interval + 1)
        verified = call(api, "POST", "/auth/mfa/verify", json={
            "mfa_token": login["mfa_token"], "code": totp.now()})
    return str(verified.json()["access_token"])


def before(base: str, email: str, password: str, state_path: str) -> None:
    api = f"{base.rstrip('/')}/api"
    hospital_id = client.get(f"{api}/auth/hospitals").json()["items"][0]["id"]
    login = call(api, "POST", "/auth/login", json={
        "hospital_id": hospital_id, "email": email, "password": password}).json()
    secret = call(api, "POST", "/auth/mfa/setup", login["access_token"]).json()["secret"]
    enabled = call(api, "POST", "/auth/mfa/enable", login["access_token"],
                   json={"code": pyotp.TOTP(secret).now()})
    check(enabled.status_code == 200, "administrator enrolled two-step sign-in")
    admin = enabled.json()["access_token"]
    kiosk = call(api, "POST", "/admin/kiosks", admin, json={"label": "Upgrade kiosk"}).json()
    paired = call(api, "POST", "/kiosks/activate",
                  json={"activation_code": kiosk["activation_code"]}).json()
    receipt = check_in(api, paired["access_token"], "cough before the upgrade")
    check(receipt["http_status"] == 200 and bool(receipt.get("token")), "patient checked in")
    sheet = call(api, "GET", f"/visits/{receipt['visit_id']}/case-sheet", admin).json()
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump({
            "api": api, "hospital_id": hospital_id, "email": email, "password": password,
            "totp_secret": secret, "device_secret": paired["device_secret"],
            "visit_id": receipt["visit_id"], "token": receipt["token"],
            "opd_card": receipt.get("opd_card"),
            "complaint": sheet["case_sheet"]["chief_complaint"],
        }, handle)


def after(state_path: str, version: str) -> None:
    state = json.load(open(state_path, encoding="utf-8"))
    api = state["api"]
    health = client.get(f"{api}/healthz").json()
    check(health.get("version") == version, f"running the new version {version}")
    admin = admin_sign_in(api, state)
    check(bool(admin), "administrator signs in with the same password and authenticator")
    visit = state["visit_id"]
    sheet = call(api, "GET", f"/visits/{visit}/case-sheet", admin)
    check(sheet.status_code == 200
          and sheet.json()["case_sheet"]["chief_complaint"] == state["complaint"],
          "the visit's case sheet is unchanged (decrypted with the same keys)")
    register = call(api, "GET", "/patients?limit=50", admin).json()["items"]
    check(any(item["token"] == state["token"] for item in register),
          "the visit is still in the register with its token")
    renewed = call(api, "POST", "/kiosks/token", json={"device_secret": state["device_secret"]})
    check(renewed.status_code == 200, "the kiosk stays paired (renews with its old secret)")
    receipt = check_in(api, renewed.json().get("access_token", ""), "fever after the upgrade")
    check(receipt["http_status"] == 200 and bool(receipt.get("token")),
          "the kiosk checks a patient in after the upgrade")
    if state.get("opd_card"):
        card = call(api, "GET", "/patients/lookup", admin,
                    params={"opd_number": state["opd_card"]["number"]}).json()["items"]
        check(len(card) == 1, "the patient's OPD card number still finds them")
    notes = call(api, "GET", f"/visits/{visit}/doctor-notes", admin)
    check(notes.status_code == 200 and notes.json()["version"] == 0,
          "routes added since the previous release work on its visits")


if __name__ == "__main__":
    if sys.argv[1:2] == ["before"]:
        before(*sys.argv[2:6])
    elif sys.argv[1:2] == ["after"]:
        after(*sys.argv[2:4])
    else:
        raise SystemExit(__doc__)
    raise SystemExit(1 if failures else 0)
