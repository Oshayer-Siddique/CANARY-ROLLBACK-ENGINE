#!/usr/bin/env python3
"""Set the local MVP Gateway canary percentage and confirm controller acceptance."""

import argparse
import json
import subprocess
import time

CONTEXT = "k3d-canary-mvp"
NAMESPACE = "canary-mvp"
ROUTE = "application-route"
GATEWAY = "canary-gateway"


def kubectl(*args):
    result = subprocess.run(
        ["kubectl", "--context", CONTEXT, "-n", NAMESPACE, "--request-timeout=15s", *args],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def get(kind, name):
    return json.loads(kubectl("get", kind, name, "-o", "json"))


def conditions_ready(conditions, generation, required):
    return all(
        any(
            c["type"] == kind
            and c["status"] == "True"
            and c.get("observedGeneration") == generation
            for c in conditions
        )
        for kind in required
    )


def backend_refs(percent):
    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise ValueError("Canary percentage must be an integer between 0 and 100")
    return [
        {"name": "application-stable", "port": 80, "weight": 100 - percent},
        {"name": "application-canary", "port": 80, "weight": percent},
    ]


def route_ready(route):
    return any(
        parent["parentRef"].get("name") == GATEWAY
        and parent["parentRef"].get("namespace", NAMESPACE) == NAMESPACE
        and parent["parentRef"].get("sectionName") == "http"
        and conditions_ready(
            parent.get("conditions", []),
            route["metadata"]["generation"],
            ("Accepted", "ResolvedRefs"),
        )
        for parent in route.get("status", {}).get("parents", [])
    )


def set_traffic(percent):
    refs = backend_refs(percent)
    route = get("httproute", ROUTE)
    if len(route["spec"]["rules"]) != 1:
        raise RuntimeError("Expected exactly one HTTPRoute rule; refusing to alter another layout")
    gateway = get("gateway", GATEWAY)
    if not conditions_ready(
        gateway.get("status", {}).get("conditions", []),
        gateway["metadata"]["generation"],
        ("Accepted", "Programmed"),
    ):
        raise RuntimeError("Gateway is not Accepted and Programmed for its current generation")
    for ref in refs:
        service = get("service", ref["name"])
        if not any(port["port"] == 80 for port in service["spec"]["ports"]):
            raise RuntimeError(f"{ref['name']} does not expose port 80")
        if ref["weight"] == 0:
            continue
        slices = json.loads(
            kubectl(
                "get",
                "endpointslices",
                "-l",
                f"kubernetes.io/service-name={ref['name']}",
                "-o",
                "json",
            )
        )
        if not any(
            ep.get("conditions", {}).get("ready") is True
            and not ep.get("conditions", {}).get("terminating", False)
            for item in slices["items"]
            for ep in item.get("endpoints", [])
        ):
            raise RuntimeError(f"{ref['name']} has no ready endpoints")
    patch = [
        {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": route["metadata"]["resourceVersion"],
        },
        {"op": "add", "path": "/spec/rules/0/backendRefs", "value": refs},
    ]
    updated = json.loads(
        kubectl("patch", "httproute", ROUTE, "--type=json", "-p", json.dumps(patch), "-o", "json")
    )
    generation = updated["metadata"]["generation"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        current = get("httproute", ROUTE)
        if current["metadata"]["generation"] != generation:
            raise RuntimeError("Another writer changed the route during verification")
        if route_ready(current):
            actual = {
                r["name"]: r.get("weight", 1) for r in current["spec"]["rules"][0]["backendRefs"]
            }
            if actual != {r["name"]: r["weight"] for r in refs}:
                raise RuntimeError(f"Unexpected route weights: {actual}")
            return {
                "stable_percent": 100 - percent,
                "canary_percent": percent,
                "generation": generation,
                "controller_accepted": True,
            }
        time.sleep(1)
    raise RuntimeError(
        "Route acceptance timed out; inspect HTTPRoute status before sending traffic"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canary-percent", type=int, required=True, choices=range(101), metavar="0..100"
    )
    args = parser.parse_args()
    try:
        print(json.dumps(set_traffic(args.canary_percent)))
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Traffic change failed: {error}\n")


if __name__ == "__main__":
    main()
