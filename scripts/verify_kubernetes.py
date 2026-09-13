"""Verify Kubernetes stable/canary workloads and the initial stable-only route."""

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

CONTEXT = "k3d-canary-mvp"
NAMESPACE = "canary-mvp"


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def kubectl(*args):
    return command("kubectl", "--context", CONTEXT, "--namespace", NAMESPACE, *args)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def forward(service):
    port = free_port()
    process = subprocess.Popen(
        [
            "kubectl",
            "--context",
            CONTEXT,
            "--namespace",
            NAMESPACE,
            "port-forward",
            f"service/{service}",
            f"{port}:80",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process, f"http://127.0.0.1:{port}"


def request(base, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(base + path, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read().decode())


def wait_for(base):
    for _ in range(60):
        try:
            if request(base, "/ready")[0] == 200:
                return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Port-forward did not become ready")


def main():
    deployments = json.loads(kubectl("get", "deployments", "-o", "json"))["items"]
    counts = {
        item["metadata"]["name"]: item["status"].get("readyReplicas", 0) for item in deployments
    }
    require(counts.get("application-v1") == 3, "Stable deployment is not ready at 3 replicas")
    require(counts.get("application-v2") == 1, "Canary deployment is not ready at 1 replica")

    processes = []
    try:
        for service, expected_version in (
            ("application-stable", "v1"),
            ("application-canary", "v2"),
            ("canary-gateway-nginx", "v1"),
        ):
            process, base = forward(service)
            processes.append(process)
            wait_for(base)
            for _ in range(10):
                status, body = request(base, "/")
                require(status == 200, f"{service} returned {status}")
                require(body["version"] == expected_version, f"{service} reached {body['version']}")
            print(f"PASS {service}: served {expected_version}")
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    print("PASS: stable/canary Services are isolated and the Gateway is 100% stable.")


if __name__ == "__main__":
    main()
