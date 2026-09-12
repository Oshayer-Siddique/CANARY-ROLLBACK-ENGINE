import logging
import random
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import Settings
from app.metrics import ApplicationMetrics, BusinessMetricsMiddleware

logger = logging.getLogger("uvicorn.error")


def create_app(
    settings: Settings | None = None,
    random_source: Callable[[], float] | None = None,
) -> FastAPI:
    settings = settings if settings is not None else Settings()
    draw = random_source if random_source is not None else random.random
    metrics = ApplicationMetrics(settings.app_version)
    demo_html = Path(__file__).with_name("templates").joinpath("demo.html").read_text()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.ready = True
        try:
            yield
        finally:
            application.state.ready = False

    application = FastAPI(
        title="Canary Demo Application",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.ready = False
    application.state.settings = settings
    application.state.metrics = metrics
    application.add_middleware(
        BusinessMetricsMiddleware,
        metrics=metrics,
        instance=settings.instance_id,
    )

    def identity():
        return {"version": settings.app_version, "instance": settings.instance_id}

    @application.get("/")
    async def business_request():
        failed = settings.failure_rate > 0 and draw() < settings.failure_rate / 100
        return JSONResponse(
            {
                **identity(),
                "message": (
                    "Simulated application failure" if failed else "Request completed successfully"
                ),
            },
            status_code=500 if failed else 200,
        )

    @application.get("/health")
    async def health():
        return JSONResponse(
            {**identity(), "status": "healthy"}, headers={"Cache-Control": "no-store"}
        )

    @application.get("/ready")
    async def ready():
        is_ready = application.state.ready
        return JSONResponse(
            {**identity(), "status": "ready" if is_ready else "not_ready"},
            status_code=200 if is_ready else 503,
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/metrics")
    async def prometheus_metrics():
        return Response(
            generate_latest(metrics.registry),
            headers={
                "Content-Type": CONTENT_TYPE_LATEST,
                "Cache-Control": "no-store",
            },
        )

    @application.get("/demo", response_class=HTMLResponse)
    async def demo():
        return HTMLResponse(demo_html, headers={"Cache-Control": "no-store"})

    @application.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("Unhandled application error", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(
            {**identity(), "message": "Internal application error"},
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )

    return application
