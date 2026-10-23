import base64
from uuid import uuid4

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.auth import PUBLIC_ROUTES, AuthenticationGateMiddleware
from app.core.security import issue_access_token
from app.main import create_app


def test_http_authentication_gate_does_not_break_websocket_routes() -> None:
    app = FastAPI()
    app.add_middleware(AuthenticationGateMiddleware)

    @app.get("/private")
    async def private_route():
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket_route(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("route-authenticated")
        await websocket.close()

    with TestClient(app) as client:
        assert client.get("/private").status_code == 401
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_text() == "route-authenticated"


class FakeConnection:
    async def execute(self, query):
        if query.text != "SELECT 1":
            raise AssertionError("unexpected readiness query")


class FakeConnectionContext:
    def __init__(self, fail=False):
        self.fail = fail

    async def __aenter__(self):
        if self.fail:
            raise ConnectionError("database unavailable")
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class FakeEngine:
    def __init__(self, fail=False):
        self.fail = fail

    def connect(self):
        return FakeConnectionContext(self.fail)

    async def dispose(self):
        return None


class FakeRedis:
    def __init__(self, fail=False):
        self.fail = fail

    async def ping(self):
        if self.fail:
            raise ConnectionError("redis unavailable")
        return True

    async def aclose(self):
        return None


def test_healthz():
    app = create_app(Settings())
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "dev"}


def test_metrics_export_uses_bounded_route_labels_and_prometheus_histogram():
    app = create_app(Settings(metrics_enabled=True))
    sample = 'http_requests_total{method="GET",route="/healthz",status="200"}'

    def healthz_count(text: str) -> float:
        line = next((line for line in text.splitlines() if line.startswith(sample)), None)
        return float(line.split()[-1]) if line else 0.0

    with TestClient(app) as client:
        before = healthz_count(client.get("/metrics").text)
        client.get("/healthz")
        record_id = uuid4()
        client.get(f"/visits/{record_id}/case-sheet")
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # Counters are process-wide (other tests add to them): compare before and after.
    assert healthz_count(response.text) == before + 1
    assert any(
        line.startswith('http_request_duration_seconds_bucket{le="+Inf",method="GET"')
        or (line.startswith("http_request_duration_seconds_bucket") and 'route="/healthz"' in line)
        for line in response.text.splitlines()
    )
    # Route templates only: a record id never becomes a label (refused before routing, the
    # request above is counted as "unmatched").
    assert str(record_id) not in response.text
    assert 'route="unmatched"' in response.text
    assert ("GET", "/metrics") in PUBLIC_ROUTES


def test_metrics_are_disabled_by_default():
    app = create_app(Settings(metrics_enabled=False))
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 404


def test_readiness_returns_503_for_dependencies_that_exceed_timeout():
    import asyncio
    from time import monotonic

    class SlowConnectionContext:
        async def __aenter__(self):
            await asyncio.sleep(5)

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class SlowEngine(FakeEngine):
        def connect(self):
            return SlowConnectionContext()

    class SlowRedis(FakeRedis):
        async def ping(self):
            await asyncio.sleep(5)

    app = create_app(
        Settings(),
        engine_factory=lambda _url: SlowEngine(),
        redis_factory=lambda _url, **_kwargs: SlowRedis(),
    )
    with TestClient(app) as client:
        # Time the readiness answer itself, not the app's start-up (which warms the PDF
        # renderer and is slow on a loaded machine).
        started = monotonic()
        response = client.get("/readyz")
        elapsed = monotonic() - started
    assert response.status_code == 503
    assert "postgres" in response.json()["detail"]
    assert "redis" in response.json()["detail"]
    assert elapsed < 2.5


def test_problem_json_and_request_id():
    app = create_app(Settings())

    @app.get("/test-error")
    async def test_error():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")

    with TestClient(app) as client:
        token = issue_access_token(
            subject=uuid4(), role="admin", hospital_id=uuid4(), settings=Settings()
        )
        response = client.get(
            "/test-error",
            headers={"X-Request-ID": "phase0-check", "Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["request_id"] == "phase0-check"


def test_validation_errors_do_not_echo_credentials():
    app = create_app(Settings())
    password = "synthetic-secret-that-must-not-appear-" + "x" * 1025
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "hospital_id": str(uuid4()),
                "email": "invalid-email",
                "password": password,
            },
        )
    assert response.status_code == 422
    assert password not in response.text
    assert "input" not in response.json()["errors"][0]


def test_security_headers():
    app = create_app(Settings())
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["permissions-policy"] == "microphone=(self)"


def test_production_hsts_and_streaming_body_limit():
    app = create_app(
        Settings(
            app_env="production",
            jwt_secret="p" * 40,
            cursor_hmac_key="c" * 40,
            app_data_key=base64.b64encode(b"d" * 32).decode(),
            app_hmac_key=base64.b64encode(b"h" * 32).decode(),
            max_request_body_bytes=1024,
        )
    )
    with TestClient(app) as client:
        response = client.get("/healthz")
        oversized = client.post("/auth/login", content=b"x" * 1025)
        openapi = client.get("/openapi.json")
    assert response.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "REQUEST_TOO_LARGE"
    assert openapi.status_code == 404


def test_login_rate_limiting_fails_closed_when_redis_is_unavailable():
    from redis.exceptions import RedisError

    class UnavailableRedis(FakeRedis):
        async def eval(self, *_args):
            raise RedisError("redis unavailable")

    app = create_app(
        Settings(),
        engine_factory=lambda _url: FakeEngine(),
        redis_factory=lambda _url, **_kwargs: UnavailableRedis(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "hospital_id": str(uuid4()),
                "email": "synthetic@example.test",
                "password": "synthetic-test-password",
            },
        )
    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"


def test_refresh_cookie_is_secure_outside_local_development():
    from fastapi import Response

    from app.api.v1.auth import _set_refresh_cookie

    response = Response()
    user_id = "22222222-2222-4222-8222-222222222222"
    _set_refresh_cookie(response, Settings(app_env="test"), "synthetic-refresh-token", user_id)
    cookie_header = response.headers["set-cookie"].lower()
    # One cookie per staff account, so several staff can work in one browser.
    assert cookie_header.startswith("osa_refresh_22222222222242228222222222222222=")
    assert "secure" in cookie_header
    assert "httponly" in cookie_header
    assert "samesite=strict" in cookie_header


def test_route_matrix_denies_anonymous_and_wrong_roles():
    settings = Settings()
    app = create_app(settings)

    def iter_routes(routes):
        for route in routes:
            if getattr(route, "methods", None):
                yield route
            nested_router = getattr(route, "original_router", None)
            if nested_router is not None:
                yield from iter_routes(nested_router.routes)

    protected = [
        (method.upper(), path, route)
        for route in iter_routes(app.routes)
        for method in route.methods
        for path in (route.path,)
        if method != "OPTIONS" and (method.upper(), path) not in PUBLIC_ROUTES
    ]
    registered = {
        (method.upper(), route.path)
        for route in iter_routes(app.routes)
        for method in route.methods
        if method != "OPTIONS"
    }
    assert PUBLIC_ROUTES <= registered
    assert protected
    candidate_roles = ("nurse", "doctor", "admin", "patient", "kiosk")

    def roles_for(route):
        allowed = set()

        def visit(dependencies):
            for dependency in dependencies:
                call = dependency.call
                if getattr(call, "__name__", "") == "role_dependency" and call.__closure__:
                    allowed.update(
                        item
                        for cell in call.__closure__
                        for item in cell.cell_contents
                        if isinstance(item, str)
                    )
                visit(dependency.dependencies)

        visit(route.dependant.dependencies)
        return allowed

    with TestClient(app) as client:
        for method, path, route in protected:
            anonymous = client.request(method, path)
            assert anonymous.status_code == 401, (method, path, anonymous.text)
            allowed = roles_for(route)
            wrong_role = next((role for role in candidate_roles if role not in allowed), None)
            if wrong_role is None:
                continue
            token = issue_access_token(
                subject=uuid4(), role=wrong_role, hospital_id=uuid4(), settings=settings
            )
            response = client.request(method, path, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 403, (method, path, response.text)


def test_readiness_reports_healthy_dependencies():
    app = create_app(
        Settings(),
        engine_factory=lambda _url: FakeEngine(),
        redis_factory=lambda _url, **_kwargs: FakeRedis(),
    )
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_names_unavailable_dependencies():
    app = create_app(
        Settings(),
        engine_factory=lambda _url: FakeEngine(fail=True),
        redis_factory=lambda _url, **_kwargs: FakeRedis(fail=True),
    )
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["detail"] == "Unavailable dependency: postgres, redis"
