"""Offline checks for routing safety and response accounting."""

import importlib.util
import json
from pathlib import Path

import pytest


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


traffic = load_script("set_canary_traffic")
generator = load_script("generate_traffic")


@pytest.mark.parametrize("value", [-1, 101, 10.5, True, "10"])
def test_invalid_percentage_rejected(value):
    with pytest.raises(ValueError):
        traffic.backend_refs(value)


@pytest.mark.parametrize("value", [0, 10, 50, 100])
def test_weights_sum_to_100(value):
    assert [r["weight"] for r in traffic.backend_refs(value)] == [100 - value, value]


def test_old_controller_status_is_not_accepted():
    conditions = [
        {"type": kind, "status": "True", "observedGeneration": 1}
        for kind in ("Accepted", "ResolvedRefs")
    ]
    assert not traffic.conditions_ready(conditions, 2, ("Accepted", "ResolvedRefs"))
    assert traffic.conditions_ready(conditions, 1, ("Accepted", "ResolvedRefs"))


def test_report_accounts_for_failures_and_invalid_bodies():
    result = generator.summarize(
        "gateway",
        [
            (200, {"version": "v1"}, None),
            (500, {"version": "v2"}, None),
            (502, None, "invalid JSON"),
            (None, None, "timeout"),
        ],
        1,
    )
    assert result["requests"] == 4
    assert result["completed"] == 3
    assert result["transport_errors"] == 1
    assert result["unexpected_responses"] == 1
    assert result["statuses_by_version"]["v2"] == {"500": 1}
    assert result["version_percentages"] == {"v1": 25, "v2": 25}


def test_restore_does_not_require_canary_endpoints(monkeypatch):
    route = {
        "metadata": {"resourceVersion": "1", "generation": 1},
        "spec": {"rules": [{"backendRefs": traffic.backend_refs(0)}]},
    }
    conditions = [
        {"type": kind, "status": "True", "observedGeneration": 1}
        for kind in ("Accepted", "Programmed")
    ]

    def get(kind, name):
        if kind == "httproute":
            return route
        if kind == "gateway":
            return {"metadata": {"generation": 1}, "status": {"conditions": conditions}}
        return {"spec": {"ports": [{"port": 80}]}}

    def kubectl(*args):
        if args[0] == "patch":
            return json.dumps(route)
        assert "kubernetes.io/service-name=application-stable" in args
        return json.dumps({"items": [{"endpoints": [{"conditions": {"ready": True}}]}]})

    monkeypatch.setattr(traffic, "get", get)
    monkeypatch.setattr(traffic, "kubectl", kubectl)
    monkeypatch.setattr(traffic, "route_ready", lambda route: True)
    assert traffic.set_traffic(0)["canary_percent"] == 0
