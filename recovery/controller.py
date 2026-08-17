"""Local MVP recovery controller: standard-library Kubernetes and Prometheus clients."""

import json
import math
import os
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROUTE_PATH = "/apis/gateway.networking.k8s.io/v1/namespaces/canary-mvp/httproutes/application-route"
MARKER = "canary-mvp/recovery"
STABLE = "application-stable"
CANARY = "application-canary"


def log(event, **fields):
    print(json.dumps({"time": time.time(), "event": event, **fields}), flush=True)


def decision(error, rate, targets, age, window=60):
    if age < window:
        return "observing"
    values = (error, rate, targets)
    if any(v is None or not math.isfinite(v) for v in values):
        return "unknown"
    if targets < 1 or rate < 0.05 or error < 0 or error > 100:
        return "unknown"
    return "unhealthy" if error > 5 else "healthy"


def weights(route):
    rules = route["spec"]["rules"]
    if len(rules) != 1:
        raise ValueError("Expected one route rule")
    refs = rules[0]["backendRefs"]
    if len(refs) != 2 or {r["name"] for r in refs} != {STABLE, CANARY}:
        raise ValueError("Expected stable and canary backends")
    for ref in refs:
        if ref.get("kind", "Service") != "Service" or ref.get("group", "") != "":
            raise ValueError("Unsupported backend kind")
        if ref.get("namespace", "canary-mvp") != "canary-mvp" or ref["port"] != 80:
            raise ValueError("Unsupported backend namespace/port")
    return {r["name"]: r.get("weight", 1) for r in refs}


def accepted(route):
    generation = route["metadata"]["generation"]
    return any(
        p["parentRef"].get("name") == "canary-gateway"
        and p["parentRef"].get("namespace", "canary-mvp") == "canary-mvp"
        and p["parentRef"].get("sectionName") == "http"
        and all(
            any(
                c["type"] == kind
                and c["status"] == "True"
                and c.get("observedGeneration") == generation
                for c in p.get("conditions", [])
            )
            for kind in ("Accepted", "ResolvedRefs")
        )
        for p in route.get("status", {}).get("parents", [])
    )


class Clients:
    def __init__(self):
        self.prometheus = os.getenv("PROMETHEUS_URL", "http://prometheus.monitoring.svc:9090")
        self.gateway = os.getenv("GATEWAY_URL", "http://canary-gateway-nginx.canary-mvp.svc")
        self.api = os.getenv("KUBERNETES_URL", "https://kubernetes.default.svc")
        self.token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        ca = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        self.ssl = ssl.create_default_context(cafile=ca) if self.api.startswith("https:") else None

    def request(self, url, method="GET", data=None, kubernetes=False):
        headers = {}
        if kubernetes and self.token_path.exists():
            headers["Authorization"] = "Bearer " + self.token_path.read_text().strip()
        if data is not None:
            headers["Content-Type"] = "application/json-patch+json"
        request = urllib.request.Request(
            url,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method,
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=self.ssl)
        )
        with opener.open(request, timeout=5) as response:
            return json.load(response)

    def route(self):
        return self.request(self.api + ROUTE_PATH, kubernetes=True)

    def patch(self, route, changes):
        test = {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": route["metadata"]["resourceVersion"],
        }
        return self.request(self.api + ROUTE_PATH, "PATCH", [test, *changes], True)

    def scalar(self, expression):
        payload = self.request(
            self.prometheus + "/api/v1/query?" + urllib.parse.urlencode({"query": expression})
        )
        if payload.get("status") != "success":
            raise RuntimeError("Prometheus query failed")
        samples = payload["data"]["result"]
        if len(samples) != 1:
            return None
        timestamp, value = samples[0]["value"]
        if abs(time.time() - float(timestamp)) > 15:
            return None
        value = float(value)
        return value if math.isfinite(value) else None

    def metrics(self):
        # Filter recording rules by source sample freshness; instant-query timestamps
        # alone do not establish freshness of lookback-selected recordings.
        error = self.scalar(
            'canary:error_percentage_60s{version="v2"} and '
            '(time() - timestamp(canary:error_percentage_60s{version="v2"}) < 15)'
        )
        rate = self.scalar(
            'canary:request_rate_60s{version="v2"} and '
            '(time() - timestamp(canary:request_rate_60s{version="v2"}) < 15)'
        )
        targets = self.scalar(
            'sum(up{job="canary-application-pods",app_version="v2"} '
            'and (time() - timestamp(up{job="canary-application-pods",'
            'app_version="v2"}) < 15))'
        )
        return error, rate, targets

    def stable_ready(self):
        dep = self.request(
            self.api + "/apis/apps/v1/namespaces/canary-mvp/deployments/application-v1",
            kubernetes=True,
        )
        return dep.get("status", {}).get("availableReplicas", 0) >= 1

    def verify_gateway(self):
        for _ in range(20):
            body = self.request(self.gateway + "/")
            if body.get("version") != "v1":
                raise RuntimeError("Gateway verification reached a non-stable version")


class Controller:
    def __init__(self, client, clock=time.monotonic):
        self.client = client
        self.clock = clock
        self.identity = None
        self.started = 0

    def step(self):
        route = self.client.route()
        current = weights(route)
        generation = route["metadata"]["generation"]
        identity = (route["metadata"]["uid"], generation)
        annotations = route["metadata"].get("annotations", {})
        marker = json.loads(annotations.get(MARKER, "{}"))
        if current[CANARY] == 0 and current[STABLE] > 0:
            self.identity = None
            if marker.get("state") == "pending" and marker.get("generation") == generation:
                if not accepted(route):
                    log("recovery_waiting_for_route", generation=generation)
                    return
                self.client.verify_gateway()
                marker["state"] = "verified"
                self.client.patch(
                    route,
                    [
                        {
                            "op": "add",
                            "path": "/metadata/annotations",
                            "value": {**annotations, MARKER: json.dumps(marker)},
                        }
                    ],
                )
                log("rollback_verified", generation=generation, requests=20)
            else:
                log("baseline", generation=generation)
            return
        if current[CANARY] <= 0 or current[STABLE] < 0:
            raise ValueError("Invalid route weights")
        if identity != self.identity:
            self.identity = identity
            self.started = self.clock()
            log("observation_started", generation=generation, window_seconds=60)
        if not accepted(route):
            log("route_not_accepted", generation=generation)
            return
        error, rate, targets = self.client.metrics()
        state = decision(error, rate, targets, self.clock() - self.started)
        log(
            state, generation=generation, error_percentage=error, request_rate=rate, targets=targets
        )
        if state != "unhealthy":
            return
        if not self.client.stable_ready():
            raise RuntimeError("Stable deployment unavailable; recovery blocked")
        refs = [
            {"name": STABLE, "port": 80, "weight": 100},
            {"name": CANARY, "port": 80, "weight": 0},
        ]
        marker = {
            "state": "pending",
            "generation": generation + 1,
            "error_percentage": error,
            "request_rate": rate,
            "requested_at": time.time(),
        }
        self.client.patch(
            route,
            [
                {"op": "add", "path": "/spec/rules/0/backendRefs", "value": refs},
                {
                    "op": "add",
                    "path": "/metadata/annotations",
                    "value": {**annotations, MARKER: json.dumps(marker)},
                },
            ],
        )
        log("rollback_requested", generation=generation, error_percentage=error)


def main():
    controller = Controller(Clients())
    while True:
        try:
            controller.step()
        except Exception as error:
            log("controller_error", error=str(error))
        time.sleep(5)


if __name__ == "__main__":
    main()
