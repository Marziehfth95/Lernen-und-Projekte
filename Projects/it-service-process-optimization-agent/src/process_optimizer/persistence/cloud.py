"""Environment-driven PostgreSQL adapter using managed-identity access tokens."""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Protocol

from sqlalchemy.engine import make_url

from process_optimizer.persistence.database import Database

POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
# Explicit, non-production opt-in: a single-replica, container-local SQLite file
# that is lost on every restart, revision change or scale-in. Never durable.
EPHEMERAL_SQLITE_DEMO_MODE = "ephemeral_sqlite_demo"
EPHEMERAL_SQLITE_DEMO_DIRECTORY = "/tmp/workflow-demo/"


class TokenProvider(Protocol):
    def __call__(self) -> str: ...


class ManagedIdentityPostgresTokenProvider:
    """Acquire short-lived PostgreSQL tokens without storing database passwords."""

    def __init__(self, client_id: str | None = None) -> None:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as error:
            raise RuntimeError("Install the project cloud extra for managed identity") from error
        self._credential = DefaultAzureCredential(managed_identity_client_id=client_id)

    def __call__(self) -> str:
        return self._credential.get_token(POSTGRES_SCOPE).token


def persistence_mode(environment: str | None = None) -> str:
    """Name the configured persistence guarantee without exposing connection details."""
    selected_environment = (environment or os.environ.get("APP_ENV", "local")).lower()
    if selected_environment != "production":
        return "local"
    if os.environ.get("WORKFLOW_PERSISTENCE_MODE") == EPHEMERAL_SQLITE_DEMO_MODE:
        return EPHEMERAL_SQLITE_DEMO_MODE
    return "durable_postgresql"


def _ephemeral_demo_database(url_value: str) -> Database:
    url = make_url(url_value)
    database_path = url.database or ""
    if (
        url.drivername != "sqlite"
        or not database_path.startswith(EPHEMERAL_SQLITE_DEMO_DIRECTORY)
        or ".." in PurePosixPath(database_path).parts
    ):
        raise ValueError(
            f"Ephemeral demo mode requires a SQLite file under {EPHEMERAL_SQLITE_DEMO_DIRECTORY}"
        )
    if os.environ.get("WORKFLOW_DATABASE_AUTH_MODE") != "none":
        raise ValueError("Ephemeral demo mode requires WORKFLOW_DATABASE_AUTH_MODE=none")
    return Database(url_value)


def database_from_environment(
    *,
    environment: str | None = None,
    token_provider: TokenProvider | None = None,
) -> Database:
    """Build the local or production adapter and reject shared SQLite in production.

    The only production SQLite path is the explicitly named ephemeral demo mode.
    """
    selected_environment = (environment or os.environ.get("APP_ENV", "local")).lower()
    url_value = os.environ.get("WORKFLOW_DATABASE_URL", "sqlite:///data/local/workflow.sqlite3")
    url = make_url(url_value)
    if selected_environment != "production":
        return Database(url_value)
    if persistence_mode(selected_environment) == EPHEMERAL_SQLITE_DEMO_MODE:
        return _ephemeral_demo_database(url_value)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Production requires postgresql+psycopg")
    if url.password is not None:
        raise ValueError("Production database URL must not contain a password")
    if os.environ.get("WORKFLOW_DATABASE_AUTH_MODE") != "entra":
        raise ValueError("Production PostgreSQL requires Entra authentication")
    provider = token_provider or ManagedIdentityPostgresTokenProvider(
        os.environ.get("AZURE_CLIENT_ID") or None
    )
    return Database(url_value, access_token_provider=provider, pool_pre_ping=True)
