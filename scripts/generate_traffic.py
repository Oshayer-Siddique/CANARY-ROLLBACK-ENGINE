#!/usr/bin/env python3
"""Send counted, optionally paced business requests to a Service or the Gateway."""

import argparse
import concurrent.futures
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

CONTEXT = "k3d-canary-mvp"
NAMESPACE = "canary-mvp"
SERVICES = {
    "stable": "application-stable",
    "canary": "application-canary",
    "gateway": "canary-gateway-nginx",
}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(url, timeout=5)
        with response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.loads(error.read().decode())


def wait_for(url):
    for _ in range(60):
        try:
            if request(url + "/ready")[0] == 200:
                return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise RuntimeError("Service port-forward did not become ready")


def summarize(target, responses, elapsed):
    result = {
        "target": target,
        "requests": len(responses),
        "completed": 0,
        "statuses": {},
        "versions": [],
        "version_counts": {},
        "version_percentages": {},
        "statuses_by_version": {},
        "transport_errors": 0,
        "unexpected_responses": 0,
        "error_examples": [],
        "elapsed_seconds": round(elapsed, 3),
    }
    for status, body, error in responses:
        if status is None:
            result["transport_errors"] += 1
        else:
            result["completed"] += 1
            key = str(status)
            result["statuses"][key] = result["statuses"].get(key, 0) + 1
            version = body.get("version") if isinstance(body, dict) else None
            if version not in ("v1", "v2"):
                result["unexpected_responses"] += 1
            else:
                counts = result["version_counts"]
                counts[version] = counts.get(version, 0) + 1
                statuses = result["statuses_by_version"].setdefault(version, {})
                statuses[key] = statuses.get(key, 0) + 1
        if error and len(result["error_examples"]) < 3:
            result["error_examples"].append(error)
    result["versions"] = sorted(result["version_counts"])
    result["version_percentages"] = {
        version: round(100 * count / len(responses), 3)
        for version, count in result["version_counts"].items()
    }
    return result


def sample(base, requests=30, workers=5, interval=0, target="gateway"):
    def send(index):
        delay = started + index * interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            try:
                response = opener.open(base + "/", timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                try:
                    return response.status, json.loads(response.read().decode()), None
                except (ValueError, UnicodeError) as error:
                    return response.status, None, str(error)
        except (OSError, urllib.error.URLError) as error:
            return None, None, str(error)

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        responses = list(executor.map(send, range(requests)))
    return summarize(target, responses, time.monotonic() - started)


def generate(target, requests=30, workers=5, interval=0):
    if requests < 1 or workers < 1 or interval < 0:
        raise ValueError("requests/workers must be positive and interval must be nonnegative")

    port = free_port()
    process = subprocess.Popen(
        [
            "kubectl",
            "--context",
            CONTEXT,
            "--namespace",
            NAMESPACE,
            "port-forward",
            f"service/{SERVICES[target]}",
            f"{port}:80",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_for(base)
        return sample(base, requests, workers, interval, target)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=SERVICES, default="canary")
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument(
        "--interval",
        type=float,
        default=0,
        help="Seconds between scheduled request starts (0 means burst)",
    )
    args = parser.parse_args()
    if args.requests < 1 or args.workers < 1 or not 0 <= args.interval < float("inf"):
        parser.error("requests/workers must be positive; interval must be finite and nonnegative")
    result = generate(args.target, args.requests, args.workers, args.interval)
    print(json.dumps(result))
    if result["transport_errors"] or result["unexpected_responses"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
