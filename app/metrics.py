import json
import logging
from time import perf_counter

from prometheus_client import CollectorRegistry, Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("uvicorn.error")


class ApplicationMetrics:
    def __init__(self, version: str):
        # Each app instance owns its registry, avoiding duplicate collectors in tests.
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "requests_total",
            "Completed business requests.",
            ["version", "route", "method", "status"],
            registry=self.registry,
        )
        self.errors = Counter(
            "http_errors_total",
            "Business requests returning HTTP 5xx.",
            ["version", "route"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "request_duration_seconds",
            "Business request duration in seconds.",
            ["version", "route"],
            registry=self.registry,
        )
        self.version = version
        for status in ("200", "500"):
            self.requests.labels(version, "/", "GET", status)
        self.errors.labels(version, "/")
        self.duration.labels(version, "/")

    def record(self, status: int, duration: float, instance: str):
        self.requests.labels(self.version, "/", "GET", str(status)).inc()
        if status >= 500:
            self.errors.labels(self.version, "/").inc()
        self.duration.labels(self.version, "/").observe(duration)
        logger.info(
            json.dumps(
                {
                    "event": "application_request",
                    "version": self.version,
                    "instance": instance,
                    "method": "GET",
                    "route": "/",
                    "status": status,
                    "duration_seconds": round(duration, 6),
                }
            )
        )


class BusinessMetricsMiddleware:
    """Instrument only GET /, including unexpected exceptions, once per response."""

    def __init__(self, app: ASGIApp, metrics: ApplicationMetrics, instance: str):
        self.app = app
        self.metrics = metrics
        self.instance = instance

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] != "/" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status = 500
        recorded = False

        def record():
            nonlocal recorded
            if not recorded:
                self.metrics.record(status, perf_counter() - started, self.instance)
                recorded = True

        async def instrumented_send(message: Message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                record()
            await send(message)

        try:
            await self.app(scope, receive, instrumented_send)
        finally:
            record()
