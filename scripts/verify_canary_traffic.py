#!/usr/bin/env python3
"""Verify baseline, 90/10 Gateway routing, Prometheus signals, and stable restoration."""

import json
import signal

from generate_traffic import generate
from set_canary_traffic import get, set_traffic
from verify_monitoring import close, forward, get_json, query, require, vector_value, wait_until


def healthy(result):
    require(result["transport_errors"] == 0, f"Transport failures: {result}")
    require(result["unexpected_responses"] == 0, f"Unexpected responses: {result}")
    require(result["statuses"] == {"200": result["requests"]}, f"HTTP failures: {result}")


def stable_only():
    # Observe data-plane convergence before the measured sample.
    wait_until(
        lambda: generate("gateway", 20)["version_counts"] == {"v1": 20},
        "Gateway did not converge to stable-only traffic",
        timeout=30,
    )
    result = generate("gateway", 200)
    healthy(result)
    require(result["version_counts"] == {"v1": 200}, f"Canary traffic remains: {result}")
    print("PASS stable-only: " + json.dumps(result), flush=True)


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    process = None
    changed = False
    try:
        for name, replicas in (("application-v1", 3), ("application-v2", 1)):
            deployment = get("deployment", name)
            require(
                deployment.get("status", {}).get("readyReplicas") == replicas,
                f"{name} must have {replicas} ready replicas",
            )
            env = deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])
            require(
                any(e["name"] == "FAILURE_RATE" and e.get("value") == "0" for e in env),
                f"{name} must use FAILURE_RATE=0 for this healthy-traffic test",
            )
        process, prometheus = forward("monitoring", "prometheus", 9090)
        wait_until(
            lambda: get_json(f"{prometheus}/api/v1/status/buildinfo")["status"] == "success",
            "Prometheus API unavailable",
        )
        for version, count in (("v1", 3), ("v2", 1)):
            expression = f'count(up{{job="canary-application-pods",app_version="{version}"}} == 1)'
            wait_until(
                lambda: vector_value(query(prometheus, expression)) == count,
                f"Prometheus must scrape {count} {version} pods",
            )
        changed = True  # Cleanup even if a patch succeeds but its status wait fails.
        print("Establishing 100/0 baseline", flush=True)
        set_traffic(0)
        stable_only()
        print("Activating 90/10 and waiting for both versions", flush=True)
        print(json.dumps(set_traffic(10)), flush=True)
        wait_until(
            lambda: set(generate("gateway", 100)["versions"]) == {"v1", "v2"},
            "Gateway did not serve both versions",
            timeout=30,
        )
        counters = (
            'sum by (version) (requests_total{job="canary-application-pods",'
            'method="GET",route="/"})'
        )
        before = query(prometheus, counters)
        result = generate("gateway", 2000)
        healthy(result)
        require(
            7 <= result["version_percentages"].get("v2", 0) <= 13,
            f"Canary share outside 7-13%: {result}",
        )
        print("PASS distribution: " + json.dumps(result), flush=True)
        print("Sending 80 seconds of paced Gateway traffic for monitoring", flush=True)
        paced = generate("gateway", 800, interval=0.1)
        healthy(paced)
        require(set(paced["versions"]) == {"v1", "v2"}, "Paced traffic missed a version")

        def metrics_ready():
            after = query(prometheus, counters)
            rates = query(prometheus, "canary:request_rate_60s")
            errors = query(prometheus, "canary:error_percentage_60s")
            return all(
                (vector_value(after, v) or 0) > (vector_value(before, v) or 0)
                and (vector_value(rates, v) or 0) > 0
                and vector_value(errors, v) == 0
                for v in ("v1", "v2")
            )

        wait_until(
            metrics_ready, "Prometheus did not show increasing healthy traffic for both versions"
        )
        require(
            not query(prometheus, 'ALERTS{alertname="CanaryErrorRateHigh",alertstate="firing"}'),
            "Unexpected canary error alert during healthy traffic",
        )
        print(
            "PASS Prometheus: both counters increased, both rates positive, both errors zero",
            flush=True,
        )
    finally:
        try:
            if changed:
                print("Restoring 100/0 and verifying fresh Gateway requests", flush=True)
                try:
                    set_traffic(0)
                    stable_only()
                except BaseException:
                    print(
                        "RESTORATION FAILED. Run: python3 scripts/set_canary_traffic.py "
                        "--canary-percent 0",
                        flush=True,
                    )
                    raise
        finally:
            if process:
                close(process)
    print("PASS Phase 5: baseline -> 90/10 -> monitoring -> verified 100/0 restoration", flush=True)


if __name__ == "__main__":
    main()
