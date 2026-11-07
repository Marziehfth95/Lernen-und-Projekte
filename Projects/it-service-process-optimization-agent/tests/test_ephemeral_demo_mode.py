"""Explicit, non-production ephemeral SQLite demo mode guardrail tests."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from process_optimizer.api import health
from process_optimizer.deployment.validation import (
    DeploymentValidationError,
    validate_deployment,
)
from process_optimizer.main import app
from process_optimizer.persistence import cloud

ROOT = Path(__file__).resolve().parents[1]
DEMO_URL = "sqlite:////tmp/workflow-demo/workflow.sqlite3"


def _production_demo_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "APP_ENV": "production",
        "ENTRA_TENANT_ID": "test-tenant",
        "ENTRA_AUDIENCE": "api-client-id",
        "ENTRA_ISSUER": "https://login.microsoftonline.com/test-tenant/v2.0",
        "ENTRA_JWKS_URL": "https://login.microsoftonline.com/test-tenant/discovery/v2.0/keys",
        "CONTAINER_APPS_AUTH_ENABLED": "true",
        "WORKFLOW_PERSISTENCE_MODE": "ephemeral_sqlite_demo",
        "WORKFLOW_DATABASE_AUTH_MODE": "none",
        "WORKFLOW_DATABASE_URL": DEMO_URL,
        "PROCESS_OPTIMIZER_ANALYSIS_DIR": "/mnt/aggregates",
        "AGGREGATE_MANIFEST_PATH": "/mnt/aggregates/aggregate_manifest.json",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("LOCAL_AUTH_IDENTITIES_JSON", raising=False)
    # Aggregate package integrity is covered elsewhere; isolate the persistence decision.
    monkeypatch.setattr(
        health.AggregateArtifactVerifier,
        "from_file",
        staticmethod(lambda *_: SimpleNamespace(verify=lambda: None)),
    )


async def _readiness() -> tuple[int, dict[str, str]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/readiness")
    return response.status_code, response.json()


def test_production_sqlite_requires_the_explicit_demo_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_demo_environment(monkeypatch)
    monkeypatch.delenv("WORKFLOW_PERSISTENCE_MODE")
    with pytest.raises(ValueError, match="postgresql"):
        cloud.database_from_environment()
    assert cloud.persistence_mode() == "durable_postgresql"

    monkeypatch.setenv("WORKFLOW_PERSISTENCE_MODE", "ephemeral_sqlite_demo")
    database = cloud.database_from_environment()
    assert database.engine.dialect.name == "sqlite"
    assert cloud.persistence_mode() == "ephemeral_sqlite_demo"


@pytest.mark.parametrize(
    ("url", "auth_mode"),
    [
        ("sqlite:///data/local/workflow.sqlite3", "none"),
        ("sqlite:////tmp/workflow-demo/../etc/workflow.sqlite3", "none"),
        ("sqlite:///:memory:", "none"),
        ("postgresql+psycopg://workflow-id@db.example/process_optimizer", "none"),
        (DEMO_URL, "entra"),
    ],
)
def test_demo_mode_accepts_only_the_container_local_sqlite_file(
    monkeypatch: pytest.MonkeyPatch, url: str, auth_mode: str
) -> None:
    _production_demo_environment(monkeypatch)
    monkeypatch.setenv("WORKFLOW_DATABASE_URL", url)
    monkeypatch.setenv("WORKFLOW_DATABASE_AUTH_MODE", auth_mode)
    with pytest.raises(ValueError, match="Ephemeral demo mode"):
        cloud.database_from_environment()


def test_readiness_labels_ephemeral_demo_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_demo_environment(monkeypatch)
    status_code, body = asyncio.run(_readiness())
    assert status_code == 200
    assert body == {
        "status": "ready",
        "environment": "production",
        "persistence": "ephemeral_sqlite_demo",
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOCAL_AUTH_IDENTITIES_JSON", '{"token": {}}'),
        ("CONTAINER_APPS_AUTH_ENABLED", "false"),
        ("WORKFLOW_DATABASE_AUTH_MODE", "entra"),
        ("WORKFLOW_DATABASE_URL", "sqlite:///data/local/workflow.sqlite3"),
    ],
)
def test_demo_mode_never_relaxes_authentication_or_storage_location(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _production_demo_environment(monkeypatch)
    monkeypatch.setenv(name, value)
    status_code, body = asyncio.run(_readiness())
    assert status_code == 503
    assert body["status"] == "not_ready"


def test_sqlite_url_without_demo_opt_in_is_not_production_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_demo_environment(monkeypatch)
    monkeypatch.delenv("WORKFLOW_PERSISTENCE_MODE")
    status_code, body = asyncio.run(_readiness())
    assert status_code == 503
    assert body["persistence"] == "durable_postgresql"


def _copy_deployment_files(target: Path) -> Path:
    for relative in ("azure.yaml", "deploy"):
        source = ROOT / relative
        destination = target / relative
        if source.is_dir():
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns("generated"))
        else:
            shutil.copy2(source, destination)
    terraform = target / "infra/terraform"
    terraform.mkdir(parents=True)
    for path in (ROOT / "infra/terraform").glob("*.tf"):
        shutil.copy2(path, terraform / path.name)
    return target


def test_durable_postgresql_must_stay_the_terraform_default(tmp_path: Path) -> None:
    root = _copy_deployment_files(tmp_path)
    variables = root / "infra/terraform/variables.tf"
    variables.write_text(
        variables.read_text(encoding="utf-8").replace(
            'default     = "postgresql"', 'default     = "ephemeral_sqlite_demo"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(DeploymentValidationError, match="Durable PostgreSQL"):
        validate_deployment(root)


def test_dedicated_workload_profiles_are_rejected(tmp_path: Path) -> None:
    root = _copy_deployment_files(tmp_path)
    main = root / "infra/terraform/main.tf"
    main.write_text(
        main.read_text(encoding="utf-8").replace(
            'workload_profile_type = "Consumption"', 'workload_profile_type = "D4"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(DeploymentValidationError, match="consumption-only"):
        validate_deployment(root)


def test_terraform_demo_mode_keeps_one_replica_and_skips_postgresql() -> None:
    main = (ROOT / "infra/terraform/main.tf").read_text(encoding="utf-8")
    # The server, database and Entra admin follow provision_postgresql, which the
    # durable mode always implies; the demo mode skips them unless explicitly staged.
    assert (
        "provision_postgresql = var.provision_postgresql || local.use_postgresql" in main
    )
    assert main.count("count                         = local.provision_postgresql ? 1 : 0") == 1
    assert main.count("count     = local.provision_postgresql ? 1 : 0") == 1
    assert main.count("count               = local.provision_postgresql ? 1 : 0") == 1
    assert "local.use_postgresql ? 1 : 0" not in main
    assert "min_replicas = local.use_postgresql ? 0 : 1" in main
    assert "max_replicas = 1" in main
    assert 'condition     = local.use_postgresql || var.environment != "prod"' in main
    assert '"sqlite:////tmp/workflow-demo/workflow.sqlite3"' in main


def test_staged_postgresql_never_switches_the_workflow_app() -> None:
    terraform = ROOT / "infra/terraform"
    main = (terraform / "main.tf").read_text(encoding="utf-8")
    jobs = (terraform / "database_jobs.tf").read_text(encoding="utf-8")
    variables = (terraform / "variables.tf").read_text(encoding="utf-8")

    assert re.search(r'variable "provision_postgresql" \{[^}]*default\s*=\s*false', variables)
    assert "var.deploy_database_jobs && local.provision_postgresql" in jobs
    # Only the persistence choice moves the app: replicas, auth mode and database URL.
    app = main.split('resource "azurerm_container_app" "workflow"', 1)[1]
    assert "provision_postgresql" not in app
    assert app.count("local.use_postgresql") >= 3


def test_postgresql_zone_is_optional_and_high_availability_stays_off() -> None:
    terraform = ROOT / "infra/terraform"
    main = (terraform / "main.tf").read_text(encoding="utf-8")
    variables = (terraform / "variables.tf").read_text(encoding="utf-8")
    server = main.split('resource "azurerm_postgresql_flexible_server" "workflow" {', 1)[1]
    server = server.split("\n}\n", 1)[0]

    assert re.search(r'variable "postgresql_zone" \{[^}]*default\s*=\s*null', variables)
    assert re.search(r"zone\s*=\s*var\.postgresql_zone", server)
    assert "high_availability" not in server
    assert 'sku_name                      = "B_Standard_B1ms"' in server
    assert 'version                       = "16"' in server
    assert "storage_mb                    = 32768" in server
    assert "public_network_access_enabled = false" in server
    assert "password_auth_enabled         = false" in server


def test_postgresql_subnet_keeps_the_azure_managed_storage_endpoint() -> None:
    main = (ROOT / "infra/terraform/main.tf").read_text(encoding="utf-8")
    subnet = main.split('resource "azurerm_subnet" "postgresql" {', 1)[1].split("\n}\n", 1)[0]

    # Azure adds it on first server provisioning; a plan that removes it breaks WAL traffic.
    assert 'service_endpoints = ["Microsoft.Storage"]' in subnet
    assert '"Microsoft.DBforPostgreSQL/flexibleServers"' in subnet
