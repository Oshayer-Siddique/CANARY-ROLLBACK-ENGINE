"""Acceptance-runner bookkeeping and cleanup tests without a cluster."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
try:
    spec = importlib.util.spec_from_file_location("verify_mvp", scripts / "verify_mvp.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
finally:
    sys.path.remove(str(scripts))


@pytest.mark.parametrize(
    "scenario,cleanup,expected",
    [
        ("passed", "passed", True),
        ("failed", "passed", False),
        ("not_run", "passed", False),
        ("passed", "failed", False),
        ("passed", "not_run", False),
        ("passed", "not_needed", False),
    ],
)
def test_acceptance_requires_scenarios_and_cleanup(scenario, cleanup, expected):
    assert (
        verifier.report_passed(
            {"scenarios": [{"status": scenario}], "cleanup": {"status": cleanup}}
        )
        == expected
    )


def test_empty_suite_never_passes():
    assert not verifier.report_passed({"scenarios": [], "cleanup": {"status": "passed"}})


def test_failed_scenario_saved_before_cleanup(tmp_path, monkeypatch):
    suite = verifier.Suite(tmp_path)
    monkeypatch.setattr(suite, "capture", lambda name: None)

    def fail():
        suite.details["measurement"] = 7
        raise AssertionError("test failure")

    with pytest.raises(AssertionError):
        suite.run_scenario("failure", fail)
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["scenarios"][0]["status"] == "failed"
    assert report["scenarios"][0]["evidence"]["measurement"] == 7
    assert report["cleanup"]["status"] == "not_run"
    assert report["passed"] is False


def test_cleanup_attempts_all_actions_after_first_failure(tmp_path, monkeypatch):
    suite = verifier.Suite(tmp_path)
    suite.original_env = []
    calls = []

    def failed_route(percent):
        calls.append("route")
        raise RuntimeError("API unavailable")

    monkeypatch.setattr(verifier, "set_traffic", failed_route)
    monkeypatch.setattr(verifier, "controller_env", lambda env: calls.append("environment"))
    monkeypatch.setattr(verifier, "failure_rate", lambda rate: calls.append("failure_rate"))
    monkeypatch.setattr(verifier, "snapshot", lambda: {"weights": {}})
    suite.cleanup()
    assert calls == ["route", "environment", "failure_rate"]
    assert suite.report["cleanup"]["status"] == "failed"


def test_environment_override_preserves_unrelated_values():
    source = [{"name": "OTHER", "value": "keep"}, {"name": "GATEWAY_URL", "value": "old"}]
    result = verifier.environment_with(source, "GATEWAY_URL", "new")
    assert result == [{"name": "OTHER", "value": "keep"}, {"name": "GATEWAY_URL", "value": "new"}]
    assert source[1]["value"] == "old"


def test_interrupted_scenario_not_marked_passed(tmp_path, monkeypatch):
    suite = verifier.Suite(tmp_path)
    monkeypatch.setattr(suite, "capture", lambda name: None)

    def interrupted():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        suite.run_scenario("interrupted", interrupted)
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["scenarios"][0]["status"] == "failed"
    assert "KeyboardInterrupt" in report["scenarios"][0]["error"]


def test_first_mutation_failure_still_requires_cleanup(tmp_path, monkeypatch):
    suite = verifier.Suite(tmp_path)

    def failed_route(percent):
        raise RuntimeError("Patch accepted but status wait failed")

    monkeypatch.setattr(verifier, "set_traffic", failed_route)
    with pytest.raises(RuntimeError):
        suite.low_traffic()
    assert suite.mutating is True


@pytest.mark.parametrize(
    "failure,expected_increase",
    [(None, True), ("initial_http", False), ("increased_http", True), ("early_decision", True)],
)
def test_healthy_release_gates_operator_increase(tmp_path, monkeypatch, failure, expected_increase):
    suite = verifier.Suite(tmp_path)
    suite.prometheus = "unused"
    changes = []
    monkeypatch.setattr(suite, "activate", lambda: (10, 100))
    monkeypatch.setattr(verifier, "failure_rate", lambda rate: None)

    def set_traffic(percent):
        changes.append(percent)
        return {"generation": 11}

    def generate(target, requests, **kwargs):
        failing = (requests == 2000 and failure == "initial_http") or (
            requests == 1200 and failure == "increased_http"
        )
        return {
            "requests": requests,
            "versions": ["v1", "v2"],
            "version_percentages": {"v2": 20 if requests == 1200 else 10},
            "statuses": {"500" if failing else "200": requests},
            "transport_errors": 0,
            "unexpected_responses": 0,
        }

    def event(name, since, generation):
        timestamp = 200 if generation == 10 else 210
        if generation == 11 and name == "healthy":
            timestamp += 59 if failure == "early_decision" else 60
        return {"time": timestamp, "generation": generation}

    monkeypatch.setattr(verifier, "set_traffic", set_traffic)
    monkeypatch.setattr(verifier, "generate", generate)
    monkeypatch.setattr(verifier, "event", event)
    monkeypatch.setattr(verifier, "query", lambda url, name: name)
    monkeypatch.setattr(
        verifier, "vector_value", lambda data, version: 1 if "request_rate" in data else 0
    )
    monkeypatch.setattr(verifier, "get", lambda *args: {})
    monkeypatch.setattr(
        verifier,
        "route_weights",
        lambda route: {
            "application-stable": 80 if 20 in changes else 90,
            "application-canary": 20 if 20 in changes else 10,
        },
    )
    if failure:
        expected_error = AssertionError if failure == "early_decision" else RuntimeError
        with pytest.raises(expected_error):
            suite.healthy_cycle()
    else:
        suite.healthy_cycle()
        assert suite.details["traffic_increase"]["decision"]["generation"] == 11
    assert (20 in changes) is expected_increase
