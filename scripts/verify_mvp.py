#!/usr/bin/env python3
"""Integrated local-MVP acceptance with reports, fault injection, and verified cleanup."""

import argparse
import fcntl
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from generate_traffic import generate
from set_canary_traffic import get, set_traffic
from verify_canary_traffic import healthy
from verify_monitoring import close, forward, get_json, query, vector_value

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "canary-mvp"
CONTROLLER = "recovery-controller"
MARKER = "canary-mvp/recovery"


def command(*args, timeout=30):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(args[:8])}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def kube(*args, namespace=NAMESPACE, timeout=30):
    return command(
        "kubectl",
        "--context",
        "k3d-canary-mvp",
        "-n",
        namespace,
        "--request-timeout=15s",
        *args,
        timeout=timeout,
    )


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def wait(predicate, message, timeout=100):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(2)
    raise TimeoutError(message)


def route_weights(route):
    return {ref["name"]: ref.get("weight", 1) for ref in route["spec"]["rules"][0]["backendRefs"]}


def marker(route):
    return json.loads(route["metadata"].get("annotations", {}).get(MARKER, "{}"))


def events():
    output = kube("logs", "deployment/" + CONTROLLER, "--tail=1000")
    parsed = []
    for line in output.splitlines():
        try:
            parsed.append(json.loads(line))
        except ValueError:
            continue
    return parsed


def event(name, since, generation=None):
    return next(
        (
            e
            for e in reversed(events())
            if e.get("event") == name
            and e.get("time", 0) >= since
            and (generation is None or e.get("generation") == generation)
        ),
        None,
    )


def failure_rate(rate):
    kube("set", "env", "deployment/application-v2", f"FAILURE_RATE={rate}")
    kube("rollout", "status", "deployment/application-v2", "--timeout=180s", timeout=200)


def controller_env(env):
    deployment = get("deployment", CONTROLLER)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    operations = [
        {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": deployment["metadata"]["resourceVersion"],
        },
        {"op": "add", "path": "/spec/template/spec/containers/0/env", "value": env},
    ]
    if container.get("env", []) != env:
        kube("patch", "deployment", CONTROLLER, "--type=json", "-p", json.dumps(operations))
        kube("rollout", "status", "deployment/" + CONTROLLER, "--timeout=180s", timeout=200)


def environment_with(original, name, value):
    return [e for e in original if e["name"] != name] + [{"name": name, "value": value}]


def restart_controller():
    kube("rollout", "restart", "deployment/" + CONTROLLER)
    kube("rollout", "status", "deployment/" + CONTROLLER, "--timeout=180s", timeout=200)


def snapshot():
    route = get("httproute", "application-route")
    workloads = {}
    for name in ("application-v1", "application-v2", CONTROLLER):
        deployment = get("deployment", name)
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        workloads[name] = {
            "generation": deployment["metadata"]["generation"],
            "replicas": deployment["spec"].get("replicas"),
            "ready": deployment.get("status", {}).get("readyReplicas", 0),
            "image": container["image"],
        }
        if name != CONTROLLER:
            workloads[name]["failure_rate"] = next(
                (e.get("value") for e in container.get("env", []) if e["name"] == "FAILURE_RATE"),
                None,
            )
    pods = json.loads(kube("get", "pods", "-o", "json"))["items"]
    return {
        "route_uid": route["metadata"]["uid"],
        "route_generation": route["metadata"]["generation"],
        "weights": route_weights(route),
        "recovery": marker(route),
        "workloads": workloads,
        "image_ids": [
            {
                "pod": pod["metadata"]["name"],
                "containers": [
                    {"name": c["name"], "image_id": c.get("imageID")}
                    for c in pod.get("status", {}).get("containerStatuses", [])
                ],
            }
            for pod in pods
        ],
    }


def final_ok(state):
    return (
        state["weights"] == {"application-stable": 100, "application-canary": 0}
        and all(
            state["workloads"][name]["ready"] == count
            for name, count in (("application-v1", 3), ("application-v2", 1), (CONTROLLER, 1))
        )
        and all(
            state["workloads"][name]["failure_rate"] == "0"
            for name in ("application-v1", "application-v2")
        )
    )


def report_passed(report):
    return (
        bool(report["scenarios"])
        and all(s["status"] == "passed" for s in report["scenarios"])
        and report.get("cleanup", {}).get("status") == "passed"
    )


class Suite:
    def __init__(self, directory, cycles=2):
        self.directory = directory
        self.cycles = cycles
        self.report = {
            "started_at": datetime.now(UTC).isoformat(),
            "context": "k3d-canary-mvp",
            "scenarios": [],
            "cleanup": {"status": "not_run"},
            "not_exercised": [
                "fresh-cluster bootstrap",
                "multi-controller leader election",
                "node/host loss",
                "force-kill cleanup",
            ],
        }
        self.original_env = None
        self.mutating = False
        self.prom_process = None
        self.prometheus = None
        self.details = {}

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.report["passed"] = report_passed(self.report)
        (self.directory / "report.json").write_text(json.dumps(self.report, indent=2) + "\n")
        lines = [
            "# MVP end-to-end verification report",
            "",
            f"Started: {self.report['started_at']}",
            "",
            f"Overall passed: **{self.report['passed']}**",
            "",
            "| Scenario | Status | Seconds |",
            "| --- | --- | --- |",
        ]
        for result in self.report["scenarios"]:
            lines.append(f"| {result['name']} | {result['status']} | {result.get('seconds', 0)} |")
        lines += [
            "",
            f"Cleanup: **{self.report['cleanup']['status']}**",
            "",
            "## Evidence",
            "",
            "See report.json for measurements, route generations, failures, and final state.",
            "Each scenario has a controller log artifact captured before cleanup.",
            "",
            "## Not exercised",
            "",
        ]
        lines += ["- " + item for item in self.report["not_exercised"]]
        (self.directory / "REPORT.md").write_text("\n".join(lines) + "\n")

    def capture(self, name):
        try:
            logs = events()
            (self.directory / (name + ".controller.json")).write_text(json.dumps(logs, indent=2))
            self.details["snapshot"] = snapshot()
        except Exception as error:
            self.details["capture_error"] = str(error)

    def run_scenario(self, name, action):
        print(f"START {name}", flush=True)
        started = time.monotonic()
        self.details = {}
        result = {"name": name, "status": "failed"}
        self.report["scenarios"].append(result)
        try:
            action()
            result["status"] = "passed"
        except BaseException as error:
            result["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            self.capture(name)
            result["seconds"] = round(time.monotonic() - started, 2)
            result["evidence"] = self.details
            self.save()
            print(f"{result['status'].upper()} {name}", flush=True)

    def preflight(self):
        state = snapshot()
        self.report["initial_state"] = state
        require(final_ok(state), "Start with ready workloads, healthy failure settings, and 100/0")
        original = get("deployment", CONTROLLER)["spec"]["template"]["spec"]["containers"][0]
        self.original_env = original.get("env", [])
        # Fault tests assume the repository's internal endpoints, not operator-specific overrides.
        require(
            not any(
                e["name"] in ("PROMETHEUS_URL", "GATEWAY_URL", "KUBERNETES_URL")
                for e in self.original_env
            ),
            "Remove custom controller URL overrides first",
        )
        self.prom_process, self.prometheus = forward("monitoring", "prometheus", 9090)

        def prom_ready():
            try:
                return get_json(self.prometheus + "/-/ready") is not None
            except (OSError, ValueError):
                # /-/ready is text, so use JSON build-info instead.
                try:
                    return (
                        get_json(self.prometheus + "/api/v1/status/buildinfo")["status"]
                        == "success"
                    )
                except (OSError, ValueError):
                    return False

        wait(prom_ready, "Prometheus API unavailable", 30)
        for version, count in (("v1", 3), ("v2", 1)):
            expression = f'count(up{{job="canary-application-pods",app_version="{version}"}} == 1)'
            wait(
                lambda: vector_value(query(self.prometheus, expression)) == count,
                f"Missing {version} scrape targets",
                60,
            )
        self.details["setup_consistency"] = {}
        for path in (
            "infra/kubernetes/base",
            "infra/kubernetes/monitoring",
            "infra/kubernetes/recovery",
        ):
            command("kubectl", "kustomize", str(ROOT / path))
            self.details["setup_consistency"][path] = "rendered"
        expected = (ROOT / "infra/kubernetes/gateway/httproute.yaml").read_text()
        require(
            "weight: 100" in expected and "weight: 0" in expected, "Baseline manifest is not 100/0"
        )
        self.details["baseline_sample"] = self.stable_sample()
        self.details["git_head"] = command("git", "rev-parse", "HEAD").strip()
        self.details["git_status"] = command("git", "status", "--short")

    def stable_sample(self, requests=200, interval=0):
        result = generate("gateway", requests, interval=interval)
        healthy(result)
        require(result["version_counts"] == {"v1": requests}, "Stable traffic was not exclusive")
        return result

    def activate(self):
        self.mutating = True
        active = set_traffic(10)
        since = time.time()
        wait(
            lambda: event("observation_started", since - 5, active["generation"]),
            "Controller did not observe activation",
            30,
        )
        return active["generation"], since

    def healthy_cycle(self):
        set_traffic(0)
        failure_rate(0)
        generation, since = self.activate()
        wait(
            lambda: set(generate("gateway", 100)["versions"]) == {"v1", "v2"},
            "Gateway did not converge to both versions",
            30,
        )
        result = generate("gateway", 2000, interval=0.065)
        self.details["traffic"] = result
        healthy(result)
        require(7 <= result["version_percentages"].get("v2", 0) <= 13, "Distribution outside 7-13%")
        health = wait(lambda: event("healthy", since, generation), "No healthy evaluation", 30)
        require(
            route_weights(get("httproute", "application-route"))["application-canary"] == 10,
            "Healthy canary was rolled back",
        )
        self.details["decision"] = health
        metrics = {
            name: query(self.prometheus, name)
            for name in ("canary:request_rate_60s", "canary:error_percentage_60s")
        }
        for version in ("v1", "v2"):
            require(
                (vector_value(metrics["canary:request_rate_60s"], version) or 0) > 0,
                "Missing positive request rate",
            )
            require(
                vector_value(metrics["canary:error_percentage_60s"], version) == 0,
                "Healthy metrics nonzero or missing",
            )
        self.details["metrics"] = metrics
        # The MVP operator increases traffic only after the 10-percent health gate.
        changed = set_traffic(20)
        observation = wait(
            lambda: event("observation_started", health["time"], changed["generation"]),
            "Traffic increase did not start a fresh observation",
            30,
        )
        traffic = generate("gateway", 1200, interval=0.067)
        self.details["traffic_increase"] = {
            "generation": changed["generation"],
            "observation": observation,
            "traffic": traffic,
        }
        healthy(traffic)
        require(
            15 <= traffic["version_percentages"].get("v2", 0) <= 25,
            "Increased canary distribution outside 15-25%",
        )
        decision = wait(
            lambda: event("healthy", observation["time"], changed["generation"]),
            "Increased traffic did not pass health evaluation",
            30,
        )
        require(
            decision["time"] - observation["time"] >= 60,
            "Increased traffic evaluated before the full observation window",
        )
        require(
            route_weights(get("httproute", "application-route"))
            == {"application-stable": 80, "application-canary": 20},
            "Healthy increased traffic was not retained",
        )
        self.details["traffic_increase"]["decision"] = decision

    def failed_cycle(self, pending=False):
        set_traffic(0)
        failure_rate(100)
        if pending:
            controller_env(environment_with(self.original_env, "GATEWAY_URL", "http://127.0.0.1:1"))
        generation, since = self.activate()
        result = generate("gateway", 1600, interval=0.067)
        self.details["traffic"] = result
        require(
            result["transport_errors"] == 0 and result["unexpected_responses"] == 0,
            "Unexpected network/response failure during injection",
        )
        require(
            result["statuses_by_version"].get("v2", {}).get("500", 0) > 0,
            "Injected v2 failures not observed",
        )

        def recovered_to(state):
            route = get("httproute", "application-route")
            m = marker(route)
            return (
                m
                if m.get("state") == state
                and m.get("generation") == generation + 1
                and route_weights(route) == {"application-stable": 100, "application-canary": 0}
                else None
            )

        if pending:
            self.details["pending_marker"] = wait(
                lambda: recovered_to("pending"), "No real pending recovery", 60
            )
            require(
                event("rollback_verified", since, generation + 1) is None,
                "Controller falsely verified blocked Gateway",
            )
            self.details["before_restart_events"] = events()
            # Restoring the Gateway URL causes a real controller pod replacement.
            controller_env(self.original_env)
        recovery = wait(lambda: recovered_to("verified"), "Automatic recovery not verified", 90)
        self.details["recovery"] = recovery
        self.details["seconds_until_request"] = round(recovery["requested_at"] - since, 2)
        verified = wait(
            lambda: event("rollback_verified", since, generation + 1), "Missing verified event", 30
        )
        self.details["verified_event"] = verified
        self.details["post_recovery_traffic"] = self.stable_sample(300, 0.1)
        require(
            len(
                [
                    e
                    for e in events()
                    if e.get("event") == "rollback_verified"
                    and e.get("generation") == generation + 1
                    and e.get("time", 0) >= since
                ]
            )
            == 1,
            "Repeated recovery verification",
        )
        require(
            get("deployment", "application-v2")["status"].get("readyReplicas") == 1,
            "Canary not retained for investigation",
        )
        failure_rate(0)

    def low_traffic(self):
        self.mutating = True  # Restore even if the first mutation or its wait fails.
        set_traffic(0)
        failure_rate(0)
        generation, since = self.activate()
        observed = wait(
            lambda: event("unknown", since, generation),
            "Insufficient traffic was not reported unknown",
            100,
        )
        require(observed.get("request_rate", 1) < 0.05, "Unknown state had another cause")
        require(event("healthy", since, generation) is None, "No-traffic release declared healthy")
        require(
            route_weights(get("httproute", "application-route"))["application-canary"] == 10,
            "No-traffic route was changed",
        )
        self.details["unknown_decision"] = observed
        set_traffic(0)

    def outage(self):
        controller_env(environment_with(self.original_env, "PROMETHEUS_URL", "http://127.0.0.1:1"))
        generation, since = self.activate()
        self.details["error"] = wait(
            lambda: event("controller_error", since),
            "Prometheus connection failure not reported",
            30,
        )
        require(
            route_weights(get("httproute", "application-route"))["application-canary"] == 10,
            "Monitoring failure changed routing",
        )
        require(event("healthy", since, generation) is None, "Monitoring outage declared healthy")
        self.details["outage_logs"] = events()
        controller_env(self.original_env)
        restarted = time.time()
        wait(
            lambda: event("observation_started", restarted - 10, generation),
            "Controller did not resume observation",
            30,
        )
        traffic = generate("gateway", 1200, interval=0.067)
        healthy(traffic)
        self.details["resumed"] = wait(
            lambda: event("healthy", restarted - 10, generation), "Monitoring did not recover", 30
        )
        set_traffic(0)

    def restart_and_manual(self):
        generation, since = self.activate()
        old = wait(
            lambda: event("observation_started", since - 5, generation),
            "Missing initial observation",
            30,
        )
        restart_controller()
        restarted = wait(
            lambda: event("observation_started", old["time"] + 0.1, generation),
            "Restart did not reset observation",
            30,
        )
        self.details["before_restart"] = old
        self.details["after_restart"] = restarted
        changed = set_traffic(20)
        changed_at = time.time()
        self.details["manual_generation"] = changed["generation"]
        fresh = wait(
            lambda: event("observation_started", changed_at - 5, changed["generation"]),
            "Manual change did not reset observation",
            30,
        )
        self.details["manual_observation"] = fresh
        data = generate("gateway", 1100, interval=0.067)
        healthy(data)
        decision = wait(
            lambda: event("healthy", fresh["time"], changed["generation"]),
            "Changed route did not finish fresh observation",
            30,
        )
        require(
            decision["time"] - fresh["time"] >= 60,
            "Controller evaluated before full restarted observation",
        )
        require(
            route_weights(get("httproute", "application-route"))["application-canary"] == 20,
            "Controller overwrote healthy manual weights",
        )
        self.details["healthy_after_manual_change"] = decision
        set_traffic(0)

    def interruption(self):
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--interrupt-probe"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=ROOT,
        )
        try:
            selector = selectors.DefaultSelector()
            selector.register(child.stdout, selectors.EVENT_READ)
            lines = []
            deadline = time.monotonic() + 45
            ready = False
            while time.monotonic() < deadline:
                if selector.select(1):
                    line = child.stdout.readline()
                    lines.append(line)
                    if "INTERRUPTION_READY" in line:
                        ready = True
                        break
                    if not line and child.poll() is not None:
                        break
            selector.close()
            require(ready, "Interruption child did not activate route")
            child.send_signal(signal.SIGTERM)
            output, _ = child.communicate(timeout=90)
            self.details["child_output"] = "".join(lines) + output
            self.details["exit_code"] = child.returncode
            require(child.returncode == 130, "Interrupted child did not exit 130 after cleanup")
            self.details["stable_sample"] = self.stable_sample()
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
                raise RuntimeError("Interruption child required force-kill; outer cleanup required")

    def cleanup(self):
        errors = []
        actions = [
            ("route", lambda: set_traffic(0)),
            ("controller_env", lambda: controller_env(self.original_env)),
            ("canary_failure_rate", lambda: failure_rate(0)),
        ]
        for name, action in actions:
            try:
                action()
            except Exception as error:
                errors.append(f"{name}: {error}")
        try:
            state = snapshot()
            self.report["final_state"] = state
            require(final_ok(state), "Final resources do not match healthy baseline")
            require(
                get("deployment", CONTROLLER)["spec"]["template"]["spec"]["containers"][0].get(
                    "env", []
                )
                == self.original_env,
                "Controller environment not restored",
            )
            self.report["cleanup_sample"] = self.stable_sample()
        except Exception as error:
            errors.append(str(error))
        self.report["cleanup"] = {"status": "failed" if errors else "passed", "errors": errors}


def interruption_probe():
    def stop(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, stop)
    cleanup_suite = Suite(ROOT / "reports")
    cleanup_suite.original_env = get("deployment", CONTROLLER)["spec"]["template"]["spec"][
        "containers"
    ][0].get("env", [])
    try:
        set_traffic(10)
        print("INTERRUPTION_READY", flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 130
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        cleanup_suite.cleanup()
        print(json.dumps(cleanup_suite.report["cleanup"]), flush=True)
        require(cleanup_suite.report["cleanup"]["status"] == "passed", "Child cleanup failed")
        print("INTERRUPTION_CLEANUP_PASSED", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cycles", type=int, default=2, help="At least two complete recovery cycles"
    )
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--interrupt-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.interrupt_probe:
        return interruption_probe()
    if args.cycles < 2:
        parser.error("--cycles must be at least 2 for repeatability")
    directory = args.report_dir or ROOT / "reports" / (
        "mvp-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    )
    require(not directory.exists(), "Report directory already exists; use a new path")
    directory.mkdir(parents=True)
    lock_path = ROOT / "reports" / ".verify-mvp.lock"
    lock_path.parent.mkdir(exist_ok=True)
    lock = lock_path.open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another local MVP verifier is running")
    suite = Suite(directory, args.cycles)

    def stop(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, stop)
    failed = False
    plan = [
        ("preflight", suite.preflight),
        ("insufficient_traffic", suite.low_traffic),
        ("prometheus_unavailable", suite.outage),
        ("restart_and_manual_change", suite.restart_and_manual),
    ]
    for index in range(args.cycles):
        plan += [
            (f"healthy_cycle_{index + 1}", suite.healthy_cycle),
            (f"recovery_cycle_{index + 1}", suite.failed_cycle),
        ]
    plan += [
        ("pending_recovery_restart", lambda: suite.failed_cycle(pending=True)),
        ("interruption_cleanup", suite.interruption),
    ]
    try:
        for name, action in plan:
            suite.run_scenario(name, action)
    except BaseException as error:
        failed = True
        print(f"FAILED: {error}", flush=True)
        completed = {r["name"] for r in suite.report["scenarios"]}
        suite.report["scenarios"] += [
            {"name": name, "status": "not_run"} for name, _ in plan if name not in completed
        ]
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if suite.mutating:
                suite.cleanup()
            else:
                suite.report["cleanup"] = {"status": "not_needed", "errors": []}
        finally:
            if suite.prom_process:
                close(suite.prom_process)
            suite.report["finished_at"] = datetime.now(UTC).isoformat()
            suite.save()
            lock.close()
    print(f"REPORT: {directory / 'REPORT.md'}", flush=True)
    return 0 if not failed and report_passed(suite.report) else 1


if __name__ == "__main__":
    sys.exit(main())
