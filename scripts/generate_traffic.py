#!/usr/bin/env python3
"""Send reproducible business traffic to either isolated application Service."""

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
SERVICES = {"stable": "application-stable", "canary": "application-canary"}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=SERVICES, default="canary")
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    if args.requests < 1 or args.workers < 1:
        raise SystemExit("--requests and --workers must be positive")

    port = free_port()
    process = subprocess.Popen(
        [
            "kubectl", "--context", CONTEXT, "--namespace", NAMESPACE,
            "port-forward", f"service/{SERVICES[args.target]}", f"{port}:80",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_for(base)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            responses = list(executor.map(lambda _: request(base + "/"), range(args.requests)))
        statuses = {}
        versions = set()
        for status, body in responses:
            statuses[str(status)] = statuses.get(str(status), 0) + 1
            versions.add(body.get("version"))
        print(
            json.dumps(
                {
                    "target": args.target,
                    "requests": args.requests,
                    "statuses": statuses,
                    "versions": sorted(versions),
                }
            )
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    main()
