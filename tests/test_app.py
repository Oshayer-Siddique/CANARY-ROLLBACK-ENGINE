import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def settings(**values):
    return Settings(_env_file=None, app_version="v2", instance_id="test-pod", **values)


@pytest.mark.parametrize("failure_rate,status", [(0, 200), (100, 500)])
def test_release_behavior(failure_rate, status):
    with TestClient(create_app(settings(failure_rate=failure_rate))) as client:
        for _ in range(10):
            response = client.get("/")
            assert response.status_code == status
            assert response.json()["version"] == "v2"
            assert response.json()["instance"] == "test-pod"
            assert response.headers["cache-control"] == "no-store"
        for path in ("/health", "/ready", "/metrics", "/demo"):
            assert client.get(path).status_code == 200


def test_partial_failure_boundary():
    draws = iter([0.0, 0.2999, 0.3, 0.9999])
    with TestClient(create_app(settings(failure_rate=30), lambda: next(draws))) as client:
        assert [client.get("/").status_code for _ in range(4)] == [500, 500, 200, 200]


def test_readiness_tracks_lifecycle():
    application = create_app(settings())
    assert application.state.ready is False
    with TestClient(application) as client:
        assert client.get("/ready").status_code == 200
        application.state.ready = False
        assert client.get("/ready").status_code == 503
        assert client.get("/health").status_code == 200
    assert application.state.ready is False


@pytest.mark.parametrize("value", [-1, 101, "broken", "nan", "inf"])
def test_invalid_failure_rate(value):
    with pytest.raises(ValidationError):
        settings(failure_rate=value)


@pytest.mark.parametrize("value", ["", " ", "v1\ninjected", "a" * 65])
def test_invalid_version(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_version=value)


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v2")
    monkeypatch.setenv("FAILURE_RATE", "30")
    monkeypatch.setenv("INSTANCE_ID", "canary-local")
    configuration = Settings(_env_file=None)
    assert configuration.app_version == "v2"
    assert configuration.failure_rate == 30
    assert configuration.instance_id == "canary-local"


def test_demo_served_without_caching():
    with TestClient(create_app(settings())) as client:
        response = client.get("/demo")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.headers["cache-control"] == "no-store"
        assert "Start requests" in response.text


def test_unexpected_error_preserves_identity_and_records_failure():
    def broken():
        raise RuntimeError("private diagnostic")

    application = create_app(settings(failure_rate=30), broken)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/")
        assert response.status_code == 500
        assert response.json()["version"] == "v2"
        assert "private diagnostic" not in response.text
        assert response.headers["cache-control"] == "no-store"
        registry = application.state.metrics.registry
        assert registry.get_sample_value("http_errors_total", {"version": "v2", "route": "/"}) == 1
