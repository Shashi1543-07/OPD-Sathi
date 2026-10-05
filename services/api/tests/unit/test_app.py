import base64
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.auth import PUBLIC_ROUTES
from app.core.security import issue_access_token
from app.main import create_app


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
    assert response.json() == {"status": "ok"}


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
    _set_refresh_cookie(response, Settings(app_env="test"), "synthetic-refresh-token")
    cookie_header = response.headers["set-cookie"].lower()
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
