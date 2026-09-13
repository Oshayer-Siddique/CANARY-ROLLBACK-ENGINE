#!/usr/bin/env python3
"""Live healthy/failed canary exercise; controller must perform the recovery."""

import json
import signal

from generate_traffic import generate
from set_canary_traffic import get, set_traffic
from verify_monitoring import kubectl, require, set_canary_failure_rate, wait_until


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, interrupted)
    touched = False
    try:
        kubectl(
            "canary-mvp", "rollout", "status", "deployment/recovery-controller", "--timeout=180s"
        )
        touched = True
        set_traffic(0)
        set_canary_failure_rate(0)
        print("Testing healthy canary for 100 seconds", flush=True)
        set_traffic(10)
        healthy = generate("gateway", 1500, interval=0.067)
        require(healthy["statuses"] == {"200": 1500}, f"Healthy HTTP failures: {healthy}")
        route = get("httproute", "application-route")
        refs = {r["name"]: r["weight"] for r in route["spec"]["rules"][0]["backendRefs"]}
        require(refs.get("application-canary") == 10, "Healthy canary was removed")
        logs = kubectl("canary-mvp", "logs", "deployment/recovery-controller", "--since=2m")
        require('"event": "healthy"' in logs, "Controller did not report healthy evaluation")
        print("PASS healthy canary remains at 90/10", flush=True)

        set_traffic(0)
        set_canary_failure_rate(100)
        active = set_traffic(10)
        print("Sending failing traffic; waiting for controller-driven restoration", flush=True)
        failed = generate("gateway", 1800, interval=0.067)
        require(
            failed["statuses_by_version"].get("v2", {}).get("500", 0) > 0,
            "No injected canary failures observed",
        )

        def recovered():
            route = get("httproute", "application-route")
            marker = json.loads(
                route["metadata"].get("annotations", {}).get("canary-mvp/recovery", "{}")
            )
            refs = {r["name"]: r["weight"] for r in route["spec"]["rules"][0]["backendRefs"]}
            return (
                refs.get("application-canary") == 0
                and marker.get("state") == "verified"
                and marker.get("generation") == active["generation"] + 1
            )

        wait_until(recovered, "Controller did not verify automatic recovery", timeout=90)
        fresh = generate("gateway", 200)
        require(
            fresh["statuses"] == {"200": 200} and fresh["version_counts"] == {"v1": 200},
            f"Restoration traffic failed: {fresh}",
        )
        print("PASS automatic recovery: " + json.dumps(fresh), flush=True)
        print(
            "PASS Phase 6: healthy release preserved, unhealthy release removed automatically",
            flush=True,
        )
    finally:
        if touched:
            try:
                set_traffic(0)
            finally:
                set_canary_failure_rate(0)
                print("Restored healthy v2 and stable-only routing", flush=True)


if __name__ == "__main__":
    main()
