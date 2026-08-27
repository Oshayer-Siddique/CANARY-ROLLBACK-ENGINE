#!/usr/bin/env python3
"""Verify Prometheus discovery, per-version metrics, and optional v2 failure recovery."""

import argparse
import json
import socket
import subprocess
import time
import urllib.parse
import urllib.request

CONTEXT = "k3d-canary-mvp"
APP_NAMESPACE = "canary-mvp"
MONITORING_NAMESPACE = "monitoring"
JOB = "canary-application-pods"


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def kubectl(namespace, *args):
    return command("kubectl", "--context", CONTEXT, "--namespace", namespace, *args)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def forward(namespace, service, remote_port):
    port = free_port()
    process = subprocess.Popen(
        [
            "kubectl",
            "--context",
            CONTEXT,
            "--namespace",
            namespace,
            "port-forward",
            f"service/{service}",
            f"{port}:{remote_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process, f"http://127.0.0.1:{port}"


def close(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def get_json(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return json.loads(response.read().decode())


def query(base, expression):
    encoded = urllib.parse.urlencode({"query": expression})
    payload = get_json(f"{base}/api/v1/query?{encoded}")
    require(payload["status"] == "success", f"Prometheus query failed: {expression}")
    return payload["data"]["result"]


def vector_value(result, label=None):
    for sample in result:
        if label is None or sample["metric"].get("version") == label:
            return float(sample["value"][1])
    return None


def wait_until(predicate, message, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(2)
    raise RuntimeError(message)


def generate(target, requests=20):
    command(
        "python3",
        "scripts/generate_traffic.py",
        "--target",
        target,
        "--requests",
        str(requests),
    )


def set_canary_failure_rate(rate):
    print(f"Setting v2 FAILURE_RATE={rate} and waiting for the rollout...", flush=True)
    kubectl(APP_NAMESPACE, "set", "env", "deployment/application-v2", f"FAILURE_RATE={rate}")
    kubectl(APP_NAMESPACE, "rollout", "status", "deployment/application-v2", "--timeout=180s")


def current_ready_canary_pod():
    payload = json.loads(
        kubectl(
            APP_NAMESPACE,
            "get",
            "pods",
            "-l",
            "app.kubernetes.io/component=canary",
            "-o",
            "json",
        )
    )
    candidates = [
        pod
        for pod in payload["items"]
        if pod["status"].get("phase") == "Running"
        and "deletionTimestamp" not in pod["metadata"]
        and any(
            condition["type"] == "Ready" and condition["status"] == "True"
            for condition in pod["status"].get("conditions", [])
        )
    ]
    require(
        len(candidates) == 1,
        "Kubernetes did not report exactly one ready, non-terminating canary pod",
    )
    return candidates[0]["metadata"]["name"]


def wait_for_canary_target(prometheus):
    pod = current_ready_canary_pod()
    expression = f'up{{job="{JOB}",pod="{pod}"}}'
    wait_until(
        lambda: vector_value(query(prometheus, expression)) == 1,
        f"Prometheus did not discover the replacement canary pod {pod}",
    )
    print(f"PASS: Prometheus is scraping replacement canary pod {pod}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exercise-canary",
        action="store_true",
        help=(
            "also prove a 100 percent failing v2 fires the alert and clears "
            "after the 60-second window"
        ),
    )
    args = parser.parse_args()

    processes = []
    try:
        print("Starting a temporary Prometheus port-forward...", flush=True)
        process, prometheus = forward(MONITORING_NAMESPACE, "prometheus", 9090)
        processes.append(process)
        wait_until(
            lambda: get_json(f"{prometheus}/api/v1/status/buildinfo")["status"] == "success",
            "Prometheus did not start",
        )

        def all_application_pods_are_ready():
            expression = f'count(up{{job="{JOB}"}} == 1)'
            return vector_value(query(prometheus, expression)) == 4

        wait_until(
            all_application_pods_are_ready,
            "Prometheus did not discover four ready application pods",
        )
        print("PASS: Prometheus discovered all three stable pods and one canary pod", flush=True)

        print("Generating healthy traffic for stable v1 and canary v2...", flush=True)
        generate("stable")
        generate("canary")
        total_requests = f'sum(increase(requests_total{{job="{JOB}",route="/"}}[60s]))'
        wait_until(
            lambda: (vector_value(query(prometheus, total_requests)) or 0) >= 30,
            "Prometheus did not collect generated business requests",
        )
        percentages = query(prometheus, "canary:error_percentage_60s")
        require(vector_value(percentages, "v1") == 0, "Healthy v1 error percentage is not zero")
        require(vector_value(percentages, "v2") == 0, "Healthy v2 error percentage is not zero")
        print(
            "PASS: healthy v1 and v2 traffic produced zero-percent 60-second error signals",
            flush=True,
        )

        if not args.exercise_canary:
            return

        print("Exercising the failure path: making v2 return HTTP 500...", flush=True)
        set_canary_failure_rate(100)
        wait_for_canary_target(prometheus)
        print(
            "Generating failing v2 traffic and waiting for Prometheus to evaluate it...", flush=True
        )
        generate("canary", 30)

        def error_signal():
            return vector_value(query(prometheus, "canary:error_percentage_60s"), "v2")

        wait_until(
            lambda: (error_signal() or 0) > 5,
            "Failing v2 did not exceed the 5-percent error threshold",
        )
        firing = 'ALERTS{alertname="CanaryErrorRateHigh",alertstate="firing"}'
        wait_until(lambda: bool(query(prometheus, firing)), "Canary error alert did not fire")
        print(
            "PASS: failing v2 exceeded the 5-percent threshold and produced the expected alert",
            flush=True,
        )

        print(
            "Restoring v2, then waiting for the 60-second health window to age out the failures...",
            flush=True,
        )
        set_canary_failure_rate(0)
        wait_for_canary_target(prometheus)
        generate("canary")
        wait_until(
            lambda: error_signal() == 0,
            "Canary error signal did not clear after the 60-second evaluation window",
            timeout=240,
        )
        require(not query(prometheus, firing), "Canary alert did not clear after recovery")
        kubectl(APP_NAMESPACE, "rollout", "status", "deployment/application-v2", "--timeout=180s")
        print(
            "PASS: restored v2 recovered and the alert cleared after the evaluation window",
            flush=True,
        )
    finally:
        if args.exercise_canary:
            try:
                set_canary_failure_rate(0)
            except subprocess.CalledProcessError:
                pass
        for process in processes:
            close(process)


if __name__ == "__main__":
    main()
