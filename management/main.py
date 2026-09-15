"""Read-only management API for the local canary rollback MVP."""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt, model_validator

CONTEXT = "k3d-canary-mvp"
APP_NAMESPACE = "canary-mvp"
ROUTE = "application-route"
GATEWAY = "canary-gateway"
CONTROLLER = "recovery-controller"
ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


class ManagementError(RuntimeError):
    """A local Kubernetes or Prometheus dependency could not be read."""


class TrafficChange(BaseModel):
    canary_percent: StrictInt = Field(ge=0, le=100)


class DemoTraffic(BaseModel):
    requests: StrictInt = Field(default=600, ge=1, le=3000)
    interval: float = Field(default=0.1, ge=0, le=1)
    workers: StrictInt = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def bounded_duration(self) -> "DemoTraffic":
        if self.requests * self.interval > 300:
            raise ValueError("Traffic duration must not exceed five minutes")
        return self


class FailureRate(BaseModel):
    failure_rate: StrictInt = Field(ge=0, le=100)


class OperationStore:
    """In-memory, single-writer operation tracker for this local operator API."""

    def __init__(self) -> None:
        self._operations: dict[str, dict[str, Any]] = {}
        self._active: str | None = None
        self._lock = threading.Lock()

    def start(self, kind: str, parameters: dict[str, Any], runner: Any) -> dict[str, Any]:
        with self._lock:
            if self._active is not None:
                raise ManagementError("Another management operation is already running")
            operation_id = str(uuid4())
            operation = {
                "id": operation_id,
                "kind": kind,
                **parameters,
                "status": "queued",
                "created_at": datetime.now(UTC).isoformat(),
            }
            self._operations[operation_id] = operation
            self._active = operation_id
        threading.Thread(target=self._run, args=(operation_id, runner), daemon=True).start()
        return operation.copy()

    def start_traffic_change(self, canary_percent: int) -> dict[str, Any]:
        def runner(progress: Any) -> dict[str, Any]:
            progress("Validating and applying the Gateway route")
            return run_json_command(
                [
                    sys.executable,
                    "scripts/set_canary_traffic.py",
                    "--canary-percent",
                    str(canary_percent),
                ],
                timeout=90,
            )

        return self.start("traffic_change", {"canary_percent": canary_percent}, runner)

    def start_demo_traffic(self, traffic: DemoTraffic) -> dict[str, Any]:
        def runner(progress: Any) -> dict[str, Any]:
            progress("Generating bounded Gateway traffic")
            return run_json_command(
                [
                    sys.executable,
                    "scripts/generate_traffic.py",
                    "--target",
                    "gateway",
                    "--requests",
                    str(traffic.requests),
                    "--workers",
                    str(traffic.workers),
                    "--interval",
                    str(traffic.interval),
                ],
                timeout=int(traffic.requests * traffic.interval + 90),
            )

        return self.start("demo_traffic", traffic.model_dump(), runner)

    def start_failure_rate(self, failure_rate: int) -> dict[str, Any]:
        def runner(progress: Any) -> dict[str, Any]:
            progress("Applying canary failure setting and waiting for rollout")
            client = LocalMvpClient()
            client.kubectl(
                APP_NAMESPACE,
                "set",
                "env",
                "deployment/application-v2",
                f"FAILURE_RATE={failure_rate}",
            )
            client.kubectl(
                APP_NAMESPACE,
                "rollout",
                "status",
                "deployment/application-v2",
                "--timeout=180s",
                timeout=200,
            )
            deployment = client.resource(APP_NAMESPACE, "deployment", "application-v2")
            summary = workload(deployment, "Canary v2")
            if summary["failure_rate"] != str(failure_rate) or not summary["available"]:
                raise ManagementError(
                    "Canary rollout completed without the requested healthy state"
                )
            return {"failure_rate": failure_rate, "ready_replicas": summary["ready_replicas"]}

        return self.start("failure_rate", {"failure_rate": failure_rate}, runner)

    def start_verification(self) -> dict[str, Any]:
        report_id = "ui-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
        report_directory = REPORTS / report_id

        def runner(progress: Any) -> dict[str, Any]:
            scenarios: list[dict[str, str]] = []
            output: list[str] = []
            progress("Starting full MVP acceptance run", scenarios=scenarios, report_id=report_id)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "scripts/verify_mvp.py",
                    "--report-dir",
                    str(report_directory),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            for line in process.stdout:
                text = line.strip()
                output.append(text)
                if text.startswith("START "):
                    scenarios.append({"name": text.removeprefix("START "), "status": "running"})
                elif text.startswith("PASSED ") or text.startswith("FAILED "):
                    status, name = text.split(" ", 1)
                    for scenario in reversed(scenarios):
                        if scenario["name"] == name:
                            scenario["status"] = status.lower()
                            break
                progress(
                    text or "Verification is running", scenarios=scenarios, report_id=report_id
                )
            code = process.wait()
            summary = report_summary(report_directory)
            if code:
                if summary:
                    progress(
                        "Verification stopped; inspect the recorded failure",
                        scenarios=summary.get("scenarios", []),
                        cleanup=summary.get("cleanup"),
                    )
                message = "\n".join(output[-12:]) or f"Verification exited {code}"
                raise ManagementError(message)
            if summary:
                progress(
                    "Verification completed",
                    scenarios=summary.get("scenarios", []),
                    cleanup=summary.get("cleanup"),
                )
            return {"report_id": report_id, "report": summary}

        return self.start("verification", {"report_id": report_id, "scenarios": []}, runner)

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            operation = self._operations.get(operation_id)
            return operation.copy() if operation else None

    def _run(self, operation_id: str, runner: Any) -> None:
        with self._lock:
            operation = self._operations[operation_id]
            operation["status"] = "running"
            operation["started_at"] = datetime.now(UTC).isoformat()
            operation["message"] = "Starting operation"
        try:

            def progress(message: str, **fields: Any) -> None:
                with self._lock:
                    operation["message"] = message
                    operation.update(fields)

            payload = runner(progress)
            with self._lock:
                operation["status"] = "succeeded"
                operation["result"] = payload
        except (ManagementError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            with self._lock:
                operation["status"] = "failed"
                operation["error"] = str(error)
        finally:
            with self._lock:
                operation["finished_at"] = datetime.now(UTC).isoformat()
                self._active = None


def run_json_command(command: list[str], timeout: int) -> dict[str, Any]:
    result = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise ManagementError(result.stderr.strip() or result.stdout.strip() or "Operation failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ManagementError("Operation returned an invalid result") from error


def report_summary(directory: Path) -> dict[str, Any] | None:
    path = directory / "report.json"
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "id": directory.name,
        "started_at": report.get("started_at"),
        "passed": report.get("passed"),
        "cleanup": report.get("cleanup", {}),
        "scenarios": report.get("scenarios", []),
    }


def reports() -> list[dict[str, Any]]:
    if not REPORTS.is_dir():
        return []
    summaries = [
        summary
        for directory in REPORTS.iterdir()
        if directory.is_dir() and (summary := report_summary(directory))
    ]
    return sorted(summaries, key=lambda item: item.get("started_at") or "", reverse=True)


def report_artifact(report_id: str, name: str) -> Path:
    if report_id != Path(report_id).name or name not in {"REPORT.md", "report.json"}:
        raise HTTPException(status_code=404, detail="Report artifact not found")
    path = REPORTS / report_id / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report artifact not found")
    return path


class LocalMvpClient:
    """Reads the existing MVP; the browser never receives cluster credentials."""

    def command(self, *args: str, timeout: int = 15) -> str:
        try:
            result = subprocess.run(
                args, text=True, capture_output=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ManagementError(str(error)) from error
        if result.returncode:
            raise ManagementError(
                result.stderr.strip() or result.stdout.strip() or "command failed"
            )
        return result.stdout

    def kubectl(self, namespace: str, *args: str, timeout: int = 15) -> str:
        return self.command(
            "kubectl",
            "--context",
            CONTEXT,
            "--namespace",
            namespace,
            "--request-timeout=10s",
            *args,
            timeout=timeout,
        )

    def resource(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        return json.loads(self.kubectl(namespace, "get", kind, name, "-o", "json"))

    def prometheus(self, expression: str, start: float | None = None) -> list[dict[str, Any]]:
        endpoint = "query_range" if start is not None else "query"
        params: dict[str, str] = {"query": expression}
        if start is not None:
            params.update({"start": str(start), "end": str(time.time()), "step": "60"})
        proxy = "/api/v1/namespaces/monitoring/services/http:prometheus:9090/proxy/api/v1/"
        payload = json.loads(
            self.command(
                "kubectl",
                "--context",
                CONTEXT,
                "get",
                "--raw",
                proxy + endpoint + "?" + urllib.parse.urlencode(params),
            )
        )
        if payload.get("status") != "success":
            raise ManagementError("Prometheus query did not succeed")
        return payload["data"]["result"]

    def events(self) -> list[dict[str, Any]]:
        events = []
        for line in self.kubectl(
            APP_NAMESPACE, "logs", "deployment/" + CONTROLLER, "--tail=80"
        ).splitlines():
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
        weights = {
            ref["name"]: ref.get("weight", 1) for ref in route["spec"]["rules"][0]["backendRefs"]
        }
        stable, canary = weights["application-stable"], weights["application-canary"]
    except (KeyError, IndexError, TypeError):
        raise ManagementError("HTTPRoute does not define stable and canary backends") from None
    generation = route["metadata"]["generation"]
    accepted = False
    for parent in route.get("status", {}).get("parents", []):
        if parent.get("parentRef", {}).get("name") != GATEWAY:
            continue
        conditions = {item.get("type"): item for item in parent.get("conditions", [])}
        accepted = all(
            conditions.get(kind, {}).get("status") == "True"
            and conditions[kind].get("observedGeneration") == generation
            for kind in ("Accepted", "ResolvedRefs")
        )
        if accepted:
            break
    return {
        "state": "available",
        "stable": stable,
        "canary": canary,
        "generation": generation,
        "accepted": accepted,
    }


def workload(deployment: dict[str, Any], name: str) -> dict[str, Any]:
    spec, status = deployment.get("spec", {}), deployment.get("status", {})
    container = spec.get("template", {}).get("spec", {}).get("containers", [{}])[0]
    env = {item.get("name"): item.get("value") for item in container.get("env", [])}
    desired, ready = spec.get("replicas", 1), status.get("readyReplicas", 0)
    return {
        "name": name,
        "desired_replicas": desired,
        "ready_replicas": ready,
        "available": ready >= desired and desired > 0,
        "image": container.get("image"),
        "failure_rate": env.get("FAILURE_RATE"),
    }


def build_overview(client: LocalMvpClient) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    problems: list[str] = []
    try:
        sections["traffic"] = route_summary(client.resource(APP_NAMESPACE, "httproute", ROUTE))
    except (ManagementError, json.JSONDecodeError) as error:
        sections["traffic"] = unavailable(str(error))
        problems.append("traffic routing")
    try:
        sections["workloads"] = {
            "state": "available",
            "stable": workload(
                client.resource(APP_NAMESPACE, "deployment", "application-v1"), "Stable v1"
            ),
            "canary": workload(
                client.resource(APP_NAMESPACE, "deployment", "application-v2"), "Canary v2"
            ),
            "controller": workload(
                client.resource(APP_NAMESPACE, "deployment", CONTROLLER), "Recovery controller"
            ),
        }
    except (ManagementError, json.JSONDecodeError) as error:
        sections["workloads"] = unavailable(str(error))
        problems.append("workloads")
    try:
        errors, rates = (
            client.prometheus("canary:error_percentage_60s"),
            client.prometheus("canary:request_rate_60s"),
        )
        sections["health"] = {
            "state": "available",
            "window_seconds": 60,
            "threshold_percent": 5,
            "versions": {
                version: {
                    "error_percent": sample_value(errors, version),
                    "request_rate": sample_value(rates, version),
                }
                for version in ("v1", "v2")
            },
            "error_history": points(
                client.prometheus("canary:error_percentage_60s", time.time() - 1800)
            ),
            "request_history": points(
                client.prometheus("canary:request_rate_60s", time.time() - 1800)
            ),
        }
    except (ManagementError, json.JSONDecodeError) as error:
        sections["health"] = unavailable(str(error))
        problems.append("Prometheus")
    try:
        events, controller = (
            client.events(),
            client.resource(APP_NAMESPACE, "deployment", CONTROLLER),
        )
        sections["controller"] = {
            "state": "available",
            "ready": controller.get("status", {}).get("readyReplicas", 0) >= 1,
            "latest_event": events[-1] if events else None,
        }
        sections["events"] = {"state": "available", "items": list(reversed(events[-12:]))}
    except (ManagementError, json.JSONDecodeError) as error:
        sections["controller"] = unavailable(str(error))
        sections["events"] = unavailable(str(error))
        problems.append("recovery controller")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "system": {
            "state": "healthy" if not problems else "degraded",
            "message": "All connected MVP services are responding"
            if not problems
            else "Unavailable: " + ", ".join(problems),
        },
        **sections,
    }


app = FastAPI(title="Canary MVP Management API", version="0.1.0")
operations = OperationStore()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/api/overview")
async def overview() -> JSONResponse:
    try:
        return JSONResponse(
            await asyncio.to_thread(build_overview, LocalMvpClient()),
            headers={"Cache-Control": "no-store"},
        )
    except Exception as error:
        return JSONResponse(
            {"system": unavailable(str(error))},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )


@app.post("/api/traffic", status_code=202)
async def change_traffic(change: TrafficChange) -> JSONResponse:
    try:
        operation = operations.start_traffic_change(change.canary_percent)
    except ManagementError as error:
        return JSONResponse({"message": str(error)}, status_code=409)
    return JSONResponse(operation, status_code=202)


@app.post("/api/demo/traffic", status_code=202)
async def generate_demo_traffic(traffic: DemoTraffic) -> JSONResponse:
    try:
        operation = operations.start_demo_traffic(traffic)
    except ManagementError as error:
        return JSONResponse({"message": str(error)}, status_code=409)
    return JSONResponse(operation, status_code=202)


@app.post("/api/demo/failure-rate", status_code=202)
async def change_failure_rate(setting: FailureRate) -> JSONResponse:
    try:
        operation = operations.start_failure_rate(setting.failure_rate)
    except ManagementError as error:
        return JSONResponse({"message": str(error)}, status_code=409)
    return JSONResponse(operation, status_code=202)


@app.post("/api/verification", status_code=202)
async def start_verification() -> JSONResponse:
    try:
        operation = operations.start_verification()
    except ManagementError as error:
        return JSONResponse({"message": str(error)}, status_code=409)
    return JSONResponse(operation, status_code=202)


@app.get("/api/reports")
async def list_reports() -> list[dict[str, Any]]:
    return reports()


@app.get("/api/reports/{report_id}/download/{name}")
async def download_report(report_id: str, name: str) -> FileResponse:
    path = report_artifact(report_id, name)
    return FileResponse(path, filename=f"{report_id}-{name}")


@app.get("/api/operations/{operation_id}")
async def operation(operation_id: str) -> dict[str, Any]:
    current = operations.get(operation_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    return current


UI_BUILD = ROOT / "frontend" / "dist"
if UI_BUILD.is_dir():
    # Mount after API routes so /api remains owned by the management service.
    app.mount("/", StaticFiles(directory=UI_BUILD, html=True), name="operator-ui")
