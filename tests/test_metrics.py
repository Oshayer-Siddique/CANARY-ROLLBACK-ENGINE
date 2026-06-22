from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.config import Settings
from app.main import create_app


def make_app(version="v2", failure_rate=0, draw=None):
    return create_app(
        Settings(
            _env_file=None,
            app_version=version,
            failure_rate=failure_rate,
            instance_id="test",
        ),
        draw,
    )


def scrape(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    return [
        sample
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    ]


def value(samples, name, **labels):
    return next(
        sample.value for sample in samples if sample.name == name and sample.labels == labels
    )


def test_exact_counts_and_duration_with_excluded_endpoints():
    draws = iter([0.1, 0.8, 0.2, 0.9])
    with TestClient(make_app(failure_rate=30, draw=lambda: next(draws))) as client:
        for _ in range(4):
            client.get("/")
        for path in ("/health", "/ready", "/metrics", "/demo", "/missing", "/docs"):
            client.get(path)
        client.post("/")
        samples = scrape(client)
        for status in ("200", "500"):
            assert (
                value(
                    samples, "requests_total", version="v2", route="/", method="GET", status=status
                )
                == 2
            )
        assert value(samples, "http_errors_total", version="v2", route="/") == 2
        assert value(samples, "request_duration_seconds_count", version="v2", route="/") == 4
        assert value(samples, "request_duration_seconds_sum", version="v2", route="/") >= 0
        assert all(s.labels.get("route", "/") == "/" for s in samples)


def test_zero_error_series_exists_before_traffic():
    with TestClient(make_app()) as client:
        samples = scrape(client)
        assert value(samples, "http_errors_total", version="v2", route="/") == 0
        assert (
            value(samples, "requests_total", version="v2", route="/", method="GET", status="200")
            == 0
        )


def test_versions_have_independent_registries():
    with TestClient(make_app("v1")) as stable, TestClient(make_app("v2", 100)) as canary:
        stable.get("/")
        canary.get("/")
        canary.get("/")
        stable_samples, canary_samples = scrape(stable), scrape(canary)
        assert value(stable_samples, "http_errors_total", version="v1", route="/") == 0
        assert value(canary_samples, "http_errors_total", version="v2", route="/") == 2
        assert all(s.labels.get("version") == "v1" for s in stable_samples)
        assert all(s.labels.get("version") == "v2" for s in canary_samples)
