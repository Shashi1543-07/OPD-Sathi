"""Hospitals, departments and patients stay apart on every route that names a record.

Phase 5 of the IIT Bombay readiness plan (synthetic data only). Each route that addresses one
record by its id is listed below with a request its owner could make. The tests send that
request as staff of another hospital, staff of another department, or another patient's
session, and expect "not found" every time, so a refusal never confirms that the record
exists. The inventory test fails when a route of this kind is added without an entry here.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from tests.integration.synthetic import (
    PatientSession,
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)

_ISOLATION_REASON = "Synthetic isolation check"
_PATCH_HEADERS = {"If-Match": '"1"', "Content-Type": "application/json-patch+json"}


@dataclass(frozen=True)
class Probe:
    """One request to a record route, valid in every respect except who is asking."""

    method: str
    path: str  # with {visit}, {alert}, {document} or {resource} in place of the record's id
    roles: tuple[str, ...]  # roles the route accepts, so only the record check can refuse
    json: Any = None
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def send(self, client: TestClient, headers: dict[str, str], **ids: str) -> Any:
        return client.request(
            self.method,
            self.path.format(**ids),
            json=self.json,
            content=self.content,
            headers={**headers, **self.headers},
        )


_STAFF = ("nurse", "doctor", "admin")
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_PHOTO = {"Content-Type": "image/jpeg"}
# Routes for staff that address one visit, one alert or one of a visit's papers.
VISIT_PROBES = (
    Probe("GET", "/visits/{visit}/case-sheet", _STAFF),
    Probe("GET", "/visits/{visit}/case-sheet.pdf", _STAFF),
    Probe(
        "PATCH",
        "/visits/{visit}/case-sheet",
        ("doctor",),
        content=json.dumps(
            [{"op": "add", "path": "/hpi/associated_symptoms/-", "value": "cough"}]
        ).encode(),
        headers=_PATCH_HEADERS,
    ),
    Probe(
        "POST",
        "/visits/{visit}/ai-summary/review",
        ("doctor",),
        json={"decision": "accepted"},
        headers={"If-Match": '"1"'},
    ),
    Probe("GET", "/visits/{visit}/history", _STAFF),
    Probe("GET", "/visits/{visit}/doctor-notes", _STAFF),
    Probe(
        "PUT",
        "/visits/{visit}/doctor-notes",
        ("doctor",),
        json={"plan": "Synthetic plan"},
        headers={"If-Match": '"0"'},
    ),
    Probe("GET", "/visits/{visit}/investigations", _STAFF),
    Probe("GET", "/visits/{visit}/fhir", ("doctor", "admin")),
    Probe("GET", "/visits/{visit}/documents", _STAFF),
    Probe(
        "POST", "/visits/{visit}/documents", ("doctor", "nurse"), content=_JPEG, headers=_PHOTO
    ),
    Probe("GET", "/visits/{visit}/documents/{document}/content", _STAFF),
    Probe(
        "POST",
        "/visits/{visit}/documents/{document}/review",
        ("doctor",),
        json={"decision": "accepted"},
    ),
    Probe("POST", "/visits/{visit}/ai-consent/withdraw", _STAFF),
    Probe("GET", "/visits/{visit}/transcript", ("doctor", "admin")),
    Probe("GET", "/visits/{visit}/queue-events", _STAFF),
    Probe("POST", "/queue/{visit}/call", _STAFF),
    Probe("POST", "/queue/{visit}/start", ("doctor", "admin")),
    Probe("POST", "/queue/{visit}/complete", ("doctor", "admin")),
    Probe("POST", "/queue/{visit}/skip", _STAFF),
    Probe("POST", "/queue/{visit}/recall", _STAFF),
    Probe("POST", "/queue/{visit}/return", _STAFF),
    Probe("POST", "/queue/{visit}/no-show", _STAFF),
    Probe("POST", "/queue/{visit}/cancel", ("nurse", "admin")),
    Probe(
        "POST",
        "/queue/{visit}/override",
        ("nurse", "admin"),
        json={"urgency": "routine", "reason": _ISOLATION_REASON},
    ),
    Probe(
        "POST",
        "/queue/{visit}/routing/confirm",
        ("nurse", "admin"),
        json={"reason": _ISOLATION_REASON},
    ),
    Probe(
        "POST",
        "/queue/{visit}/link-patient",
        ("nurse", "admin"),
        json={"opd_number": "26-000001"},
    ),
    Probe("POST", "/alerts/{alert}/ack", _STAFF),
)
# Administrator routes that address one department, staff account or kiosk.
ADMIN_PROBES = (
    Probe("PATCH", "/admin/departments/{resource}", ("admin",), json={"name_en": "Renamed"}),
    Probe("PATCH", "/admin/users/{resource}", ("admin",), json={"name": "Renamed"}),
    Probe("PATCH", "/admin/kiosks/{resource}", ("admin",), json={"label": "Renamed"}),
    Probe("POST", "/admin/kiosks/{resource}/activation-code", ("admin",)),
    Probe("GET", "/admin/kiosks/{resource}/checkin-link", ("admin",)),
    Probe("POST", "/admin/kiosks/{resource}/checkin-link", ("admin",)),
    Probe("DELETE", "/admin/kiosks/{resource}", ("admin",)),
    Probe("DELETE", "/admin/users/{resource}", ("admin",)),
    Probe("POST", "/admin/users/{resource}/password-reset", ("admin",)),
    Probe("POST", "/admin/users/{resource}/unlock", ("admin",)),
    Probe("POST", "/admin/users/{resource}/mfa-reset", ("admin",)),
    Probe("GET", "/admin/users/{resource}/sessions", ("admin",)),
    Probe("POST", "/admin/users/{resource}/sessions/end", ("admin",)),
    Probe("DELETE", "/admin/departments/{resource}", ("admin",)),
)
# A patient's own session: the patient token names exactly one visit.
SESSION_PROBES = (
    Probe("POST", "/intake/sessions/{visit}/language", ("patient",), json={"language": "en"}),
    Probe(
        "POST",
        "/intake/sessions/{visit}/consent",
        ("patient",),
        json={"version": "v1", "sha256": "0" * 64, "granted": True},
    ),
    Probe(
        "POST",
        "/intake/sessions/{visit}/consent/ai",
        ("patient",),
        json={"version": "v1", "sha256": "0" * 64, "granted": True},
    ),
    Probe(
        "POST", "/intake/sessions/{visit}/identity", ("patient",), json={"age": 30, "sex": "male"}
    ),
    Probe(
        "POST",
        "/intake/sessions/{visit}/opd-card",
        ("patient",),
        json={"number": "26-000001", "pin": "123456"},
    ),
    Probe("POST", "/intake/sessions/{visit}/follow-up", ("patient",), json={}),
    Probe("POST", "/intake/sessions/{visit}/danger-signs", ("patient",), json={"tile_ids": []}),
    Probe("GET", "/intake/sessions/{visit}/danger-tiles", ("patient",)),
    Probe("GET", "/intake/sessions/{visit}/interview/next-question", ("patient",)),
    Probe(
        "POST",
        "/intake/sessions/{visit}/interview/answers",
        ("patient",),
        json={"question_id": "chief_concern", "answer": "fever"},
        headers={"Idempotency-Key": "isolation-probe"},
    ),
    Probe(
        "PATCH",
        "/intake/sessions/{visit}/interview/answers",
        ("patient",),
        json={"question_id": "chief_concern", "answer": "fever"},
    ),
    Probe("POST", "/intake/sessions/{visit}/interview/back", ("patient",)),
    Probe("GET", "/intake/sessions/{visit}/readback-audio", ("patient",)),
    Probe("POST", "/intake/sessions/{visit}/help", ("patient",)),
    Probe("POST", "/intake/sessions/{visit}/needs-assistance", ("patient",)),
    Probe("POST", "/intake/sessions/{visit}/confirm", ("patient",)),
    Probe("DELETE", "/intake/sessions/{visit}", ("patient",)),
    Probe(
        "POST",
        "/intake/sessions/{visit}/transcribe",
        ("patient",),
        content=b"\x00\x00" * 64,
        headers={"Content-Type": "audio/webm"},
    ),
    Probe("POST", "/intake/sessions/{visit}/speak", ("patient",), json={"kind": "consent"}),
    Probe(
        "POST",
        "/intake/sessions/{visit}/prescription-photo",
        ("patient",),
        content=_JPEG,
        headers=_PHOTO,
    ),
    Probe(
        "POST", "/intake/sessions/{visit}/documents", ("patient",), content=_JPEG, headers=_PHOTO
    ),
    Probe("GET", "/intake/sessions/{visit}/documents", ("patient",)),
    Probe("DELETE", "/intake/sessions/{visit}/documents/{document}", ("patient",)),
)
# Routes whose record is named by an opaque secret in the path instead of an id; they are
# covered by the status-page and phone check-in tests.
_SECRET_ROUTES = {
    "/status/{token}",
    "/status/{token}/stream",
    "/status/{token}/ai-consent/withdraw",
    "/status/{token}/sms/stop",
    "/intake/checkin/{code}",
}
_PATIENT_SOCKET = "/ws/intake/{visit_id}/audio"


def _template(path: str) -> str:
    return (
        path.replace("{visit}", "{visit_id}")
        .replace("{alert}", "{alert_id}")
        .replace("{document}", "{document_id}")
        .replace("{resource}", "{resource_id}")
    )


def _all_routes(routes: list[Any]) -> Iterator[Any]:
    for route in routes:
        yield route
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _all_routes(nested.routes)


def test_every_record_route_is_in_the_isolation_matrix() -> None:
    """A new route that names a record by id must be added to the probes above."""
    app = create_app(Settings())
    record_ids = ("{visit_id}", "{alert_id}", "{resource_id}")
    routes = set()
    sockets = set()
    for route in _all_routes(app.routes):
        if isinstance(route, APIRoute) and any(part in route.path for part in record_ids):
            routes.update((method, route.path) for method in route.methods if method != "HEAD")
        elif isinstance(route, APIWebSocketRoute) and any(
            part in route.path for part in record_ids
        ):
            sockets.add(route.path)
    probed = {
        (probe.method, _template(probe.path))
        for probe in (*VISIT_PROBES, *SESSION_PROBES, *ADMIN_PROBES)
    }
    # One delete route serves departments, users and kiosks.
    probed = {
        ("DELETE", "/admin/{resource}/{resource_id}")
        if method == "DELETE" and path.startswith("/admin/")
        else (method, path)
        for method, path in probed
    }
    assert routes == probed, {"not probed": routes - probed, "no such route": probed - routes}
    assert sockets == {_PATIENT_SOCKET}
    assert not any(path in _SECRET_ROUTES for _, path in probed)


def _queued_visit(
    client: TestClient, hospital: SyntheticHospital, *, tiles: tuple[str, ...] = ("chest_pain",)
) -> tuple[PatientSession, dict[str, Any], str]:
    """A confirmed visit in the queue, with one photo of an old paper the patient added."""
    session = start_kiosk_session(client, activate_kiosk(client, hospital))
    complete_intake(session, complaint="fever and cough", tiles=tiles, phone="9876500011")
    paper = client.post(
        f"/intake/sessions/{session.visit_id}/documents?kind=lab_report",
        content=_JPEG,
        headers={**session.headers(), **_PHOTO},
    )
    assert paper.status_code == 201, paper.text
    receipt = session.confirm()
    assert receipt.status_code == 200, receipt.text
    return session, dict(receipt.json()), str(paper.json()["id"])


def _alert_id(client: TestClient, hospital: SyntheticHospital, visit_id: str) -> str:
    alerts = client.get("/alerts", headers=hospital.staff("nurse")).json()["items"]
    return str(next(alert["id"] for alert in alerts if alert["visit_id"] == visit_id))


async def _visit_record(hospital: SyntheticHospital, visit_id: str) -> dict[str, Any]:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT v.status, v.urgency, v.patient_id, v.department_id, cs.version, "
        "(SELECT count(*) FROM red_flag_events e WHERE e.hospital_id = v.hospital_id "
        "AND e.visit_id = v.id AND e.acknowledged_at IS NOT NULL) AS acknowledged "
        "FROM visits v JOIN case_sheets cs ON cs.hospital_id = v.hospital_id "
        "AND cs.visit_id = v.id WHERE v.hospital_id = :h AND v.id = :v",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return dict(rows[0])


async def _papers(hospital: SyntheticHospital, visit_id: str) -> int:
    """How many photos of old papers the visit has (refused requests add or remove none)."""
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT count(*) AS n FROM documents d JOIN document_contents c "
        "ON c.hospital_id = d.hospital_id AND c.document_id = d.id "
        "WHERE d.hospital_id = :h AND d.visit_id = :v",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return int(rows[0]["n"])


def _first_allowed(probe: Probe, preference: tuple[str, ...]) -> str:
    return next(role for role in preference if role in probe.roles)


async def test_another_hospital_cannot_reach_any_record() -> None:
    async with synthetic_hospital() as hospital, synthetic_hospital() as other:
        with api_client(hospital.settings) as client:
            session, _, paper = _queued_visit(client, hospital)
            visit = session.visit_id
            alert = _alert_id(client, hospital, visit)
            before = await _visit_record(hospital, visit)
            for probe in VISIT_PROBES:
                # Staff of the other hospital with a role the route accepts.
                who = _first_allowed(probe, ("admin", "nurse", "doctor"))
                response = probe.send(
                    client, other.staff(who), visit=visit, alert=alert, document=paper
                )
                assert response.status_code == 404, (probe.method, probe.path, response.text)

            point = client.post(
                "/admin/kiosks",
                json={"label": "Waiting hall poster", "kind": "mobile"},
                headers=hospital.staff("admin"),
            )
            assert point.status_code == 201, point.text
            targets = {
                "/admin/departments/{resource}": str(hospital.departments["ORTHO"]),
                "/admin/users/{resource}": str(hospital.users["nurse"]),
                "/admin/users/{resource}/password-reset": str(hospital.users["nurse"]),
                "/admin/users/{resource}/unlock": str(hospital.users["nurse"]),
                "/admin/users/{resource}/mfa-reset": str(hospital.users["nurse"]),
                "/admin/users/{resource}/sessions": str(hospital.users["nurse"]),
                "/admin/users/{resource}/sessions/end": str(hospital.users["nurse"]),
                "/admin/kiosks/{resource}": str(hospital.kiosk_id),
                "/admin/kiosks/{resource}/activation-code": str(hospital.kiosk_id),
                "/admin/kiosks/{resource}/checkin-link": point.json()["id"],
            }
            for probe in ADMIN_PROBES:
                response = probe.send(client, other.staff("admin"), resource=targets[probe.path])
                assert response.status_code == 404, (probe.method, probe.path, response.text)

            # Nor can the other hospital move its own staff into this hospital's department.
            moved = client.patch(
                f"/admin/users/{other.users['nurse']}",
                json={"department_id": str(hospital.departments["GENMED"])},
                headers=other.staff("admin"),
            )
            assert moved.status_code in {404, 422}, moved.text

        assert await _visit_record(hospital, visit) == before
        assert await _papers(hospital, visit) == 1
        changed = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT (SELECT count(*) FROM users WHERE hospital_id = :h "
            "AND (name = 'Renamed' OR deleted_at IS NOT NULL)) AS users, "
            "(SELECT count(*) FROM departments WHERE hospital_id = :h "
            "AND (name_en = 'Renamed' OR deleted_at IS NOT NULL)) AS departments, "
            "(SELECT count(*) FROM kiosks WHERE hospital_id = :h "
            "AND (label = 'Renamed' OR deleted_at IS NOT NULL)) AS kiosks",
            h=hospital.hospital_id,
        )
        assert dict(changed[0]) == {"users": 0, "departments": 0, "kiosks": 0}
        # The refused requests left no trace in this hospital's audit trail.
        foreign = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT count(*) AS n FROM audit_log WHERE hospital_id = :h AND actor_id IN "
            "(:a, :b, :c)",
            h=hospital.hospital_id,
            a=other.users["admin"],
            b=other.users["nurse"],
            c=other.users["doctor"],
        )
        assert foreign[0]["n"] == 0


async def test_another_department_cannot_reach_a_visit() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session, _, paper = _queued_visit(client, hospital)
            visit = session.visit_id
            alert = _alert_id(client, hospital, visit)
            before = await _visit_record(hospital, visit)
            for probe in VISIT_PROBES:
                # Orthopaedics staff with a role the route accepts; administrators act
                # hospital-wide by design, so they are not part of this check.
                who = "ortho_doctor" if "doctor" in probe.roles else "ortho_nurse"
                response = probe.send(
                    client, hospital.staff(who), visit=visit, alert=alert, document=paper
                )
                assert response.status_code == 404, (probe.method, probe.path, response.text)
        assert await _visit_record(hospital, visit) == before
        assert await _papers(hospital, visit) == 1


async def test_a_patient_session_reaches_only_its_own_visit() -> None:
    async with synthetic_hospital() as hospital, synthetic_hospital() as other:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            mine = start_kiosk_session(client, kiosk)
            complete_intake(mine, complaint="fever")
            theirs, _, paper = _queued_visit(client, hospital)
            elsewhere = start_kiosk_session(client, activate_kiosk(client, other))
            before = await _visit_record(hospital, theirs.visit_id)
            for probe in SESSION_PROBES:
                for target in (theirs.visit_id, elsewhere.visit_id):
                    response = probe.send(client, mine.headers(), visit=target, document=paper)
                    assert response.status_code == 404, (probe.method, probe.path, response.text)
            # Nor can a session reach another visit's paper through its own visit.
            taken = client.delete(
                f"/intake/sessions/{mine.visit_id}/documents/{paper}", headers=mine.headers()
            )
            assert taken.status_code == 404, taken.text
            # The kiosk that started a session is not the patient either.
            for probe in SESSION_PROBES:
                if probe.method == "DELETE" and probe.path == "/intake/sessions/{visit}":
                    continue  # A kiosk may end the session it started (tested elsewhere).
                response = probe.send(client, kiosk, visit=mine.visit_id, document=paper)
                assert response.status_code == 403, (probe.method, probe.path, response.text)
            # Staff routes refuse patient sessions outright.
            for probe in VISIT_PROBES:
                response = probe.send(
                    client,
                    mine.headers(),
                    visit=mine.visit_id,
                    alert=mine.visit_id,
                    document=paper,
                )
                assert response.status_code == 403, (probe.method, probe.path, response.text)
            with client.websocket_connect(f"/ws/intake/{theirs.visit_id}/audio") as socket:
                socket.send_json({"type": "auth", "token": mine.token})
                with pytest.raises(WebSocketDisconnect) as closed:
                    socket.receive_json()
            assert closed.value.code == 4403
        assert await _visit_record(hospital, theirs.visit_id) == before
        assert await _papers(hospital, theirs.visit_id) == 1
        assert await _papers(hospital, mine.visit_id) == 0


async def test_lists_never_show_another_hospitals_or_departments_patients() -> None:
    async with synthetic_hospital() as hospital, synthetic_hospital() as other:
        with api_client(hospital.settings) as client:
            session, receipt, _ = _queued_visit(client, hospital)
            visit, token = session.visit_id, receipt["token"]
            genmed = str(hospital.departments["GENMED"])

            def shown(response: Any) -> bool:
                assert response.status_code == 200, response.text
                return visit in response.text or f'"{token}"' in response.text

            # The owner's department sees the patient (so the checks below are meaningful).
            assert shown(client.get("/queue", headers=hospital.staff("nurse")))
            assert shown(client.get("/patients", headers=hospital.staff("doctor")))
            assert shown(client.get("/alerts", headers=hospital.staff("nurse")))

            for headers in (other.staff("admin"), hospital.staff("ortho_doctor")):
                assert not shown(client.get("/queue", headers=headers))
                assert not shown(client.get("/patients", headers=headers))
                assert not shown(client.get("/alerts", headers=headers))
            # Asking for this hospital's department by id shows nothing from another hospital.
            refused = client.get(f"/queue?department_id={genmed}", headers=other.staff("admin"))
            assert refused.status_code == 404
            assert not shown(
                client.get(f"/alerts?department_id={genmed}", headers=other.staff("admin"))
            )
            # Staff of another department cannot ask for this one.
            for path in ("/queue", "/alerts"):
                response = client.get(
                    f"{path}?department_id={genmed}", headers=hospital.staff("ortho_nurse")
                )
                assert response.status_code == 403, (path, response.text)
            lookup = client.get("/patients/lookup?phone=9876500011", headers=other.staff("nurse"))
            assert lookup.status_code == 200 and lookup.json()["items"] == []
            audit = client.get("/audit?limit=100", headers=other.staff("admin"))
            assert audit.status_code == 200 and visit not in audit.text

            # The live board: another hospital's snapshot is empty, another department is shut.
            with client.websocket_connect(f"/ws/staff/queue?department_id={genmed}") as socket:
                socket.send_json({"type": "auth", "token": other.token("admin")})
                snapshot = socket.receive_json()
            assert snapshot["type"] == "queue.snapshot" and snapshot["items"] == []
            with client.websocket_connect(f"/ws/staff/queue?department_id={genmed}") as socket:
                socket.send_json({"type": "auth", "token": hospital.token("ortho_nurse")})
                with pytest.raises(WebSocketDisconnect) as closed:
                    socket.receive_json()
            assert closed.value.code == 4403
