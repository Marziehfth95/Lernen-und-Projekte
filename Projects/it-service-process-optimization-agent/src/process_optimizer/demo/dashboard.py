"""Local-only dashboard: the production routers plus one read-only demo router.

This is a separate FastAPI application from ``process_optimizer.main``. The
production app is never imported or modified by this module, so enabling the
Phase 8A demo can never change production wiring, routes, or middleware.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from process_optimizer import __version__
from process_optimizer.api.aggregate import router as aggregate_router
from process_optimizer.api.health import router as health_router
from process_optimizer.api.workflow import router as workflow_router
from process_optimizer.demo.api import router as demo_router
from process_optimizer.observability import CorrelationAndTelemetryMiddleware

_STATIC_DIR = Path(__file__).parent / "static"


def create_dashboard_app() -> FastAPI:
    """Build the LOCAL demo dashboard application; never used in production."""
    app = FastAPI(
        title="IT Service Process Optimizer -- LOCAL DEMO (MOCK agent, not production)",
        version=__version__,
    )
    app.include_router(health_router)
    app.include_router(workflow_router)
    app.include_router(aggregate_router)
    app.include_router(demo_router)
    app.add_middleware(CorrelationAndTelemetryMiddleware)
    app.mount("/demo/ui", StaticFiles(directory=_STATIC_DIR, html=True), name="demo-ui")
    return app
