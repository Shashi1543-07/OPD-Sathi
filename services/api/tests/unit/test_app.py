from fastapi.testclient import TestClient

from app.config import Settings
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
        response = client.get("/test-error", headers={"X-Request-ID": "phase0-check"})
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["request_id"] == "phase0-check"


def test_security_headers():
    app = create_app(Settings())
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


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
