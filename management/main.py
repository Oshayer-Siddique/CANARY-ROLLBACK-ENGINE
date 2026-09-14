"""Read-only management API for the local canary rollback MVP."""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
import time
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

CONTEXT = "k3d-canary-mvp"
APP_NAMESPACE = "canary-mvp"
ROUTE = "application-route"
GATEWAY = "canary-gateway"
CONTROLLER = "recovery-controller"


class ManagementError(RuntimeError):
    """A local Kubernetes or Prometheus dependency could not be read."""


class LocalMvpClient:
    """Reads the existing MVP; the browser never receives cluster credentials."""

    def command(self, *args: str, timeout: int = 15) -> str:
        try:
            result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ManagementError(str(error)) from error
        if result.returncode:
            raise ManagementError(result.stderr.strip() or result.stdout.strip() or "command failed")
        return result.stdout

    def kubectl(self, namespace: str, *args: str) -> str:
        return self.command("kubectl", "--context", CONTEXT, "--namespace", namespace, "--request-timeout=10s", *args)

    def resource(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        return json.loads(self.kubectl(namespace, "get", kind, name, "-o", "json"))

    def prometheus(self, expression: str, start: float | None = None) -> list[dict[str, Any]]:
        endpoint = "query_range" if start is not None else "query"
        params: dict[str, str] = {"query": expression}
        if start is not None:
            params.update({"start": str(start), "end": str(time.time()), "step": "60"})
        proxy = "/api/v1/namespaces/monitoring/services/http:prometheus:9090/proxy/api/v1/"
        payload = json.loads(self.command("kubectl", "--context", CONTEXT, "get", "--raw", proxy + endpoint + "?" + urllib.parse.urlencode(params)))
        if payload.get("status") != "success":
            raise ManagementError("Prometheus query did not succeed")
        return payload["data"]["result"]

    def events(self) -> list[dict[str, Any]]:
        events = []
        for line in self.kubectl(APP_NAMESPACE, "logs", "deployment/" + CONTROLLER, "--tail=80").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and "event" in event:
                events.append(event)
        return events


def unavailable(message: str) -> dict[str, str]:
    return {"state": "unavailable", "message": message}


def sample_value(samples: list[dict[str, Any]], version: str) -> float | None:
    for sample in samples:
        if sample.get("metric", {}).get("version") == version:
            try:
                number = float(sample["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None
    return None


def points(samples: list[dict[str, Any]]) -> dict[str, list[dict[str, float]]]:
    result: dict[str, list[dict[str, float]]] = {"v1": [], "v2": []}
    for sample in samples:
        version = sample.get("metric", {}).get("version")
        if version not in result:
            continue
        for timestamp, raw_value in sample.get("values", []):
            try:
                point = {"timestamp": float(timestamp), "value": float(raw_value)}
            except (TypeError, ValueError):
                continue
            if math.isfinite(point["timestamp"]) and math.isfinite(point["value"]):
                result[version].append(point)
    return result


def route_summary(route: dict[str, Any]) -> dict[str, Any]:
    try:
        weights = {ref["name"]: ref.get("weight", 1) for ref in route["spec"]["rules"][0]["backendRefs"]}
        stable, canary = weights["application-stable"], weights["application-canary"]
    except (KeyError, IndexError, TypeError):
        raise ManagementError("HTTPRoute does not define stable and canary backends") from None
    generation = route["metadata"]["generation"]
    accepted = False
    for parent in route.get("status", {}).get("parents", []):
        if parent.get("parentRef", {}).get("name") != GATEWAY:
            continue
        conditions = {item.get("type"): item for item in parent.get("conditions", [])}
        accepted = all(conditions.get(kind, {}).get("status") == "True" and conditions[kind].get("observedGeneration") == generation for kind in ("Accepted", "ResolvedRefs"))
        if accepted:
            break
    return {"state": "available", "stable": stable, "canary": canary, "generation": generation, "accepted": accepted}


def workload(deployment: dict[str, Any], name: str) -> dict[str, Any]:
    spec, status = deployment.get("spec", {}), deployment.get("status", {})
    container = spec.get("template", {}).get("spec", {}).get("containers", [{}])[0]
    env = {item.get("name"): item.get("value") for item in container.get("env", [])}
    desired, ready = spec.get("replicas", 1), status.get("readyReplicas", 0)
    return {"name": name, "desired_replicas": desired, "ready_replicas": ready, "available": ready >= desired and desired > 0, "image": container.get("image"), "failure_rate": env.get("FAILURE_RATE")}


def build_overview(client: LocalMvpClient) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    problems: list[str] = []
    try:
        sections["traffic"] = route_summary(client.resource(APP_NAMESPACE, "httproute", ROUTE))
    except (ManagementError, json.JSONDecodeError) as error:
        sections["traffic"] = unavailable(str(error)); problems.append("traffic routing")
    try:
        sections["workloads"] = {"state": "available", "stable": workload(client.resource(APP_NAMESPACE, "deployment", "application-v1"), "Stable v1"), "canary": workload(client.resource(APP_NAMESPACE, "deployment", "application-v2"), "Canary v2"), "controller": workload(client.resource(APP_NAMESPACE, "deployment", CONTROLLER), "Recovery controller")}
    except (ManagementError, json.JSONDecodeError) as error:
        sections["workloads"] = unavailable(str(error)); problems.append("workloads")
    try:
        errors, rates = client.prometheus("canary:error_percentage_60s"), client.prometheus("canary:request_rate_60s")
        sections["health"] = {"state": "available", "window_seconds": 60, "threshold_percent": 5, "versions": {version: {"error_percent": sample_value(errors, version), "request_rate": sample_value(rates, version)} for version in ("v1", "v2")}, "error_history": points(client.prometheus("canary:error_percentage_60s", time.time() - 1800)), "request_history": points(client.prometheus("canary:request_rate_60s", time.time() - 1800))}
    except (ManagementError, json.JSONDecodeError) as error:
        sections["health"] = unavailable(str(error)); problems.append("Prometheus")
    try:
        events, controller = client.events(), client.resource(APP_NAMESPACE, "deployment", CONTROLLER)
        sections["controller"] = {"state": "available", "ready": controller.get("status", {}).get("readyReplicas", 0) >= 1, "latest_event": events[-1] if events else None}
        sections["events"] = {"state": "available", "items": list(reversed(events[-12:]))}
    except (ManagementError, json.JSONDecodeError) as error:
        sections["controller"] = unavailable(str(error)); sections["events"] = unavailable(str(error)); problems.append("recovery controller")
    return {"generated_at": datetime.now(UTC).isoformat(), "system": {"state": "healthy" if not problems else "degraded", "message": "All connected MVP services are responding" if not problems else "Unavailable: " + ", ".join(problems)}, **sections}


app = FastAPI(title="Canary MVP Management API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET"], allow_headers=[])


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/api/overview")
async def overview() -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(build_overview, LocalMvpClient()), headers={"Cache-Control": "no-store"})
    except Exception as error:
        return JSONResponse({"system": unavailable(str(error))}, status_code=503, headers={"Cache-Control": "no-store"})
