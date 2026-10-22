"""Staff account security end to end (Phase 7, tasks 7.3.1-7.3.5): synthetic data only.

- An administrator must enrol two-step sign-in before doing anything else; afterwards every
  sign-in needs a one-time code, a code works once, and a recovery code works once.
- A new account and a reset password must be replaced by the person at their first sign-in.
- A reset, an MFA reset, deactivation and "sign out everywhere" end the person's existing
  sessions at once, including access tokens that have not expired yet.
"""

import time
from typing import Any
from uuid import uuid4

import pyotp
import redis
from fastapi.testclient import TestClient

from tests.integration.synthetic import (
    STAFF_PASSWORD,
    SyntheticHospital,
    api_client,
    fetch,
    synthetic_hospital,
)

NEW_PASSWORD = "lantern orchard river 2031"


def _email(hospital: SyntheticHospital, name: str) -> str:
    return f"{name}-{hospital.hospital_id}@example.test"


def _login(client: TestClient, hospital: SyntheticHospital, name: str, password: str) -> Any:
    _clear_rate_limits(hospital)
    return client.post(
        "/auth/login",
        json={
            "hospital_id": str(hospital.hospital_id),
            "email": _email(hospital, name),
            "password": password,
        },
    )


def _clear_rate_limits(hospital: SyntheticHospital) -> None:
    """Every test client shares one address; these tests are about accounts, not limits."""
    client = redis.Redis.from_url(hospital.settings.redis_state_url)
    try:
        keys = list(client.scan_iter(f"osa:{hospital.settings.app_env}:rl:*"))
        if keys:
            client.delete(*keys)
    finally:
        client.close()


def _bearer(token: str, *, idempotent: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idempotent:
        headers["Idempotency-Key"] = uuid4().hex
    return headers


def _next_code(secret: str) -> str:
    """A code for the next time step: valid now, and never one already used."""
    return pyotp.TOTP(secret).at(time.time() + 30)


async def _actions(hospital: SyntheticHospital) -> list[str]:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT action FROM audit_log WHERE hospital_id = :h ORDER BY created_at",
        h=hospital.hospital_id,
    )
    return [row["action"] for row in rows]


async def test_administrator_enrols_mfa_then_needs_a_code_every_sign_in() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            first = _login(client, hospital, "admin", STAFF_PASSWORD)
            assert first.status_code == 200, first.text
            assert first.json()["restrictions"] == ["mfa_setup"]
            token = first.json()["access_token"]
            blocked = client.get("/admin/users", headers=_bearer(token))
            assert blocked.status_code == 403 and blocked.json()["code"] == "MFA_SETUP_REQUIRED"
            assert client.get("/auth/me", headers=_bearer(token)).json()["mfa_required"] is True

            setup = client.post("/auth/mfa/setup", headers=_bearer(token))
            assert setup.status_code == 200 and setup.headers["cache-control"] == "no-store"
            secret = setup.json()["secret"]
            wrong = client.post("/auth/mfa/enable", json={"code": "000000"}, headers=_bearer(token))
            assert wrong.status_code == 422
            enabled = client.post(
                "/auth/mfa/enable",
                json={"code": pyotp.TOTP(secret).now()},
                headers=_bearer(token),
            )
            assert enabled.status_code == 200, enabled.text
            recovery = enabled.json()["recovery_codes"]
            assert len(recovery) == 10 and enabled.json()["restrictions"] == []
            assert client.get(
                "/admin/users", headers=_bearer(enabled.json()["access_token"])
            ).status_code == 200

            # The next sign-in stops at a challenge: no session without the code.
            challenged = _login(client, hospital, "admin", STAFF_PASSWORD)
            assert challenged.json()["mfa_required"] is True
            assert "access_token" not in challenged.json()
            mfa_token = challenged.json()["mfa_token"]
            bad = client.post("/auth/mfa/verify", json={"mfa_token": mfa_token, "code": "111111"})
            assert bad.status_code == 401 and bad.json()["code"] == "INVALID_MFA_CODE"
            good = client.post(
                "/auth/mfa/verify", json={"mfa_token": mfa_token, "code": _next_code(secret)}
            )
            assert good.status_code == 200, good.text
            assert good.json()["user"]["mfa_enabled"] is True
            # A challenge opens one session only.
            replay = client.post(
                "/auth/mfa/verify", json={"mfa_token": mfa_token, "code": _next_code(secret)}
            )
            assert replay.status_code == 401

            # A recovery code works once.
            again = _login(client, hospital, "admin", STAFF_PASSWORD).json()["mfa_token"]
            used = client.post(
                "/auth/mfa/verify", json={"mfa_token": again, "recovery_code": recovery[0]}
            )
            assert used.status_code == 200
            once_more = _login(client, hospital, "admin", STAFF_PASSWORD).json()["mfa_token"]
            reused = client.post(
                "/auth/mfa/verify", json={"mfa_token": once_more, "recovery_code": recovery[0]}
            )
            assert reused.status_code == 401
            me = client.get("/auth/me", headers=_bearer(used.json()["access_token"])).json()
            assert me["recovery_codes_left"] == 9
            # An administrator cannot switch off what their role requires.
            off = client.post(
                "/auth/mfa/disable",
                json={"code": _next_code(secret)},
                headers=_bearer(used.json()["access_token"]),
            )
            assert off.status_code == 409
        actions = await _actions(hospital)
        assert "auth.mfa.enabled" in actions and "auth.mfa.failed" in actions
        assert "auth.login.mfa_challenged" in actions


async def test_wrong_codes_lock_the_account_like_wrong_passwords() -> None:
    settings_override = {"mfa_required_roles": "admin"}
    async with synthetic_hospital() as hospital:
        hospital.settings = hospital.settings.model_copy(update=settings_override)
        with api_client(hospital.settings) as client:
            token = _login(client, hospital, "doctor", STAFF_PASSWORD).json()["access_token"]
            secret = client.post("/auth/mfa/setup", headers=_bearer(token)).json()["secret"]
            client.post(
                "/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()}, headers=_bearer(token)
            )
            challenge = _login(client, hospital, "doctor", STAFF_PASSWORD).json()["mfa_token"]
            statuses = []
            for _ in range(5):
                _clear_rate_limits(hospital)
                statuses.append(
                    client.post(
                        "/auth/mfa/verify", json={"mfa_token": challenge, "code": "123456"}
                    ).status_code
                )
            assert statuses[-1] == 423
            # Even the right code is refused while locked.
            locked = client.post(
                "/auth/mfa/verify", json={"mfa_token": challenge, "code": _next_code(secret)}
            )
            assert locked.status_code == 423


async def test_new_account_and_reset_password_must_be_replaced_at_first_sign_in() -> None:
    async with synthetic_hospital() as hospital:
        hospital.settings = hospital.settings.model_copy(update={"mfa_required_roles": ""})
        with api_client(hospital.settings) as client:
            admin = _login(client, hospital, "admin", STAFF_PASSWORD).json()["access_token"]
            weak = client.post(
                "/admin/users",
                json={
                    "role": "nurse",
                    "name": "Synthetic New Nurse",
                    "email": "new-nurse@example.test",
                    "password": "Password@1234",
                    "department_id": str(hospital.departments["GENMED"]),
                },
                headers=_bearer(admin, idempotent=True),
            )
            assert weak.status_code == 422 and weak.json()["code"] == "PASSWORD_TOO_WEAK"

            # Reset the doctor, who is signed in elsewhere: that session ends at once.
            doctor = _login(client, hospital, "doctor", STAFF_PASSWORD).json()["access_token"]
            assert client.get("/auth/me", headers=_bearer(doctor)).status_code == 200
            reset = client.post(
                f"/admin/users/{hospital.users['doctor']}/password-reset",
                headers=_bearer(admin, idempotent=True),
            )
            assert reset.status_code == 200, reset.text
            temporary = reset.json()["temporary_password"]
            assert client.get("/auth/me", headers=_bearer(doctor)).status_code == 401
            assert _login(client, hospital, "doctor", STAFF_PASSWORD).status_code == 401

            restricted = _login(client, hospital, "doctor", temporary).json()
            assert restricted["restrictions"] == ["password_change"]
            blocked = client.get("/queue", headers=_bearer(restricted["access_token"]))
            assert blocked.status_code == 403
            assert blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
            too_weak = client.post(
                "/auth/password",
                json={"current_password": temporary, "new_password": "Synthetic@123456"},
                headers=_bearer(restricted["access_token"]),
            )
            assert too_weak.status_code == 422
            changed = client.post(
                "/auth/password",
                json={"current_password": temporary, "new_password": NEW_PASSWORD},
                headers=_bearer(restricted["access_token"]),
            )
            assert changed.status_code == 200, changed.text
            assert changed.json()["restrictions"] == []
            assert _login(client, hospital, "doctor", NEW_PASSWORD).status_code == 200
            # An administrator resets others, never themselves.
            own = client.post(
                f"/admin/users/{hospital.users['admin']}/password-reset",
                headers=_bearer(admin, idempotent=True),
            )
            assert own.status_code == 409
        actions = await _actions(hospital)
        assert "admin.users.password_reset" in actions and "auth.password.changed" in actions


async def test_sessions_are_listed_and_ended_and_deactivation_signs_out_at_once() -> None:
    async with synthetic_hospital() as hospital:
        hospital.settings = hospital.settings.model_copy(update={"mfa_required_roles": ""})
        with api_client(hospital.settings) as client:
            admin = _login(client, hospital, "admin", STAFF_PASSWORD).json()["access_token"]
            nurse = _login(client, hospital, "nurse", STAFF_PASSWORD).json()["access_token"]
            listed = client.get(
                f"/admin/users/{hospital.users['nurse']}/sessions", headers=_bearer(admin)
            )
            assert listed.status_code == 200 and len(listed.json()["items"]) == 1
            assert listed.json()["items"][0]["device"]
            mine = client.get("/auth/sessions", headers=_bearer(nurse)).json()["items"]
            assert len(mine) == 1 and mine[0]["current"] is True

            # Locked out: an administrator lifts it.
            for _ in range(5):
                _login(client, hospital, "doctor", "wrong-password-attempt")
            assert _login(client, hospital, "doctor", STAFF_PASSWORD).status_code == 423
            unlocked = client.post(
                f"/admin/users/{hospital.users['doctor']}/unlock", headers=_bearer(admin)
            )
            assert unlocked.status_code == 200
            assert _login(client, hospital, "doctor", STAFF_PASSWORD).status_code == 200

            ended = client.post(
                f"/admin/users/{hospital.users['nurse']}/sessions/end", headers=_bearer(admin)
            )
            assert ended.status_code == 200 and ended.json()["ended"] >= 1
            assert client.get("/auth/me", headers=_bearer(nurse)).status_code == 401

            nurse = _login(client, hospital, "nurse", STAFF_PASSWORD).json()["access_token"]
            off = client.patch(
                f"/admin/users/{hospital.users['nurse']}",
                json={"is_active": False},
                headers=_bearer(admin, idempotent=True),
            )
            assert off.status_code == 200
            assert client.get("/auth/me", headers=_bearer(nurse)).status_code == 401
        actions = await _actions(hospital)
        assert {"admin.users.unlocked", "admin.users.sessions_ended"} <= set(actions)
