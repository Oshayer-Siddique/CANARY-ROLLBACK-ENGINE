"""Policy and controller failure tests, without Kubernetes."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "controller", Path(__file__).resolve().parents[1] / "recovery/controller.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "error,rate,targets,age,expected",
    [
        (0, 1, 1, 60, "healthy"),
        (5, 1, 1, 60, "healthy"),
        (5.01, 1, 1, 60, "unhealthy"),
        (100, 1, 1, 59, "observing"),
        (None, 1, 1, 60, "unknown"),
        (float("nan"), 1, 1, 60, "unknown"),
        (0, 0, 1, 60, "unknown"),
        (100, 1, 0, 60, "unknown"),
        (100, None, 1, 60, "unknown"),
        (float("inf"), 1, 1, 60, "unknown"),
    ],
)
def test_policy(error, rate, targets, age, expected):
    assert module.decision(error, rate, targets, age) == expected


class Fake:
    def __init__(self):
        self.data = {
            "metadata": {"uid": "route", "generation": 1, "resourceVersion": "1"},
            "spec": {
                "rules": [
                    {
                        "backendRefs": [
                            {"name": module.STABLE, "port": 80, "weight": 90},
                            {"name": module.CANARY, "port": 80, "weight": 10},
                        ]
                    }
                ]
            },
        }
        self.health = (100, 1, 1)
        self.writes = 0
        self.ready = True
        self.fail_patch = False
        self.fail_verify = False
        self.sync()

    def sync(self):
        self.data["status"] = {
            "parents": [
                {
                    "parentRef": {"name": "canary-gateway", "sectionName": "http"},
                    "conditions": [
                        {
                            "type": k,
                            "status": "True",
                            "observedGeneration": self.data["metadata"]["generation"],
                        }
                        for k in ("Accepted", "ResolvedRefs")
                    ],
                }
            ]
        }

    def route(self):
        return copy.deepcopy(self.data)

    def metrics(self):
        return self.health

    def stable_ready(self):
        return self.ready

    def patch(self, route, changes):
        if self.fail_patch:
            raise RuntimeError("conflict")
        assert route["metadata"]["resourceVersion"] == self.data["metadata"]["resourceVersion"]
        for change in changes:
            if change["path"] == "/spec/rules/0/backendRefs":
                self.data["spec"]["rules"][0]["backendRefs"] = change["value"]
                self.data["metadata"]["generation"] += 1
            else:
                self.data["metadata"]["annotations"] = change["value"]
        self.writes += 1
        self.data["metadata"]["resourceVersion"] = str(self.writes + 1)
        self.sync()

    def verify_gateway(self):
        if self.fail_verify:
            raise RuntimeError("gateway failed")


def test_recovery_pending_survives_restart_and_verified_once():
    client = Fake()
    now = [0]
    controller = module.Controller(client, lambda: now[0])
    controller.step()
    assert client.writes == 0
    now[0] = 60
    controller.step()
    assert client.writes == 1
    restarted = module.Controller(client)
    restarted.step()
    marker = json.loads(client.data["metadata"]["annotations"][module.MARKER])
    assert marker["state"] == "verified"
    restarted.step()
    assert client.writes == 2


def test_conflict_is_not_reported_as_success():
    client = Fake()
    now = [0]
    controller = module.Controller(client, lambda: now[0])
    controller.step()
    now[0] = 60
    client.fail_patch = True
    with pytest.raises(RuntimeError, match="conflict"):
        controller.step()
    assert client.writes == 0


def test_missing_metrics_never_patch():
    client = Fake()
    client.health = (None, None, None)
    now = [0]
    controller = module.Controller(client, lambda: now[0])
    controller.step()
    now[0] = 90
    controller.step()
    assert client.writes == 0


def test_generation_change_restarts_observation():
    client = Fake()
    now = [0]
    controller = module.Controller(client, lambda: now[0])
    controller.step()
    now[0] = 90
    client.data["metadata"]["generation"] += 1
    client.sync()
    controller.step()
    assert client.writes == 0


def test_failed_gateway_verification_stays_pending():
    client = Fake()
    now = [0]
    controller = module.Controller(client, lambda: now[0])
    controller.step()
    now[0] = 60
    controller.step()
    client.fail_verify = True
    with pytest.raises(RuntimeError, match="gateway failed"):
        controller.step()
    assert json.loads(client.data["metadata"]["annotations"][module.MARKER])["state"] == "pending"


def test_unavailable_stable_prevents_patch():
    client = Fake()
    now = [0]
    controller = module.Controller(client, lambda: now[0])
    controller.step()
    now[0] = 60
    client.ready = False
    with pytest.raises(RuntimeError, match="Stable deployment"):
        controller.step()
    assert client.writes == 0


def test_prometheus_outage_never_patches():
    client = Fake()

    def unavailable():
        raise OSError("Prometheus unavailable")

    client.metrics = unavailable
    controller = module.Controller(client)
    with pytest.raises(OSError, match="Prometheus unavailable"):
        controller.step()
    assert client.writes == 0


def test_unaccepted_recovery_never_marked_verified():
    client = Fake()
    now = [0]
    controller = module.Controller(client, lambda: now[0])
    controller.step()
    now[0] = 60
    controller.step()
    client.data["status"]["parents"][0]["conditions"][0]["observedGeneration"] = 1
    controller.step()
    assert client.writes == 1
    assert json.loads(client.data["metadata"]["annotations"][module.MARKER])["state"] == "pending"
