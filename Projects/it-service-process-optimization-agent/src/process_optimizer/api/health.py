"""Service health endpoint."""

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from process_optimizer.persistence.cloud import (
    EPHEMERAL_SQLITE_DEMO_DIRECTORY,
    EPHEMERAL_SQLITE_DEMO_MODE,
    persistence_mode,
)
from process_optimizer.tools.manifest import AggregateArtifactVerifier

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Explicit liveness response."""

    status: Literal["healthy"]


class ReadinessResponse(BaseModel):
    """Non-sensitive configuration readiness response."""

    status: Literal["ready", "not_ready"]
    environment: str
    persistence: Literal["local", "durable_postgresql", "ephemeral_sqlite_demo"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report that the API process is running."""
    return HealthResponse(status="healthy")


@router.get("/ready", response_model=ReadinessResponse, include_in_schema=False)
@router.get("/readiness", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    """Check production configuration and governed artifacts without live calls.

    ``/readiness`` is the canonical probe path. ``/ready`` remains as a hidden
    compatibility alias for existing local automation.
    """
    environment = os.environ.get("APP_ENV", "local").lower()
    required = (
        "ENTRA_TENANT_ID",
        "ENTRA_AUDIENCE",
        "ENTRA_ISSUER",
        "ENTRA_JWKS_URL",
        "CONTAINER_APPS_AUTH_ENABLED",
        "WORKFLOW_DATABASE_URL",
        "WORKFLOW_DATABASE_AUTH_MODE",
        "PROCESS_OPTIMIZER_ANALYSIS_DIR",
        "AGGREGATE_MANIFEST_PATH",
    )
    persistence = persistence_mode(environment)
    ready = environment != "production" or all(os.environ.get(name) for name in required)
    if environment == "production" and ready:
        database_url = os.environ["WORKFLOW_DATABASE_URL"]
        database_ready = (
            database_url.startswith(f"sqlite:///{EPHEMERAL_SQLITE_DEMO_DIRECTORY}")
            and os.environ.get("WORKFLOW_DATABASE_AUTH_MODE") == "none"
            if persistence == EPHEMERAL_SQLITE_DEMO_MODE
            else database_url.startswith("postgresql+psycopg://")
            and os.environ.get("WORKFLOW_DATABASE_AUTH_MODE") == "entra"
        )
        ready = (
            database_ready
            and os.environ.get("CONTAINER_APPS_AUTH_ENABLED") == "true"
            and not os.environ.get("LOCAL_AUTH_IDENTITIES_JSON")
        )
    if environment == "production" and ready:
        try:
            AggregateArtifactVerifier.from_file(
                Path(os.environ["PROCESS_OPTIMIZER_ANALYSIS_DIR"]),
                Path(os.environ["AGGREGATE_MANIFEST_PATH"]),
            ).verify()
        except (OSError, ValueError):
            ready = False
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        environment=environment,
        persistence=persistence,
    )
