"""FastAPI application entry point."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from process_optimizer import __version__
from process_optimizer.api.aggregate import router as aggregate_router
from process_optimizer.api.health import router as health_router
from process_optimizer.api.workflow import router as workflow_router
from process_optimizer.observability import (
    CorrelationAndTelemetryMiddleware,
    configure_observability,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure optional cloud telemetry without making it a local dependency."""
    configure_observability()
    yield

# Production serves no unauthenticated schema or docs: /health and /readiness stay the only
# routes that answer without a validated token.
_docs_enabled = os.environ.get("APP_ENV", "local").lower() != "production"

app = FastAPI(
    title="IT Service Process Optimization Agent",
    description="Governed analysis of the overall IT Incident Management process.",
    version=__version__,
    lifespan=lifespan,
    openapi_url="/openapi.json" if _docs_enabled else None,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
)
app.include_router(health_router)
app.include_router(workflow_router)
app.include_router(aggregate_router)
app.add_middleware(CorrelationAndTelemetryMiddleware)
