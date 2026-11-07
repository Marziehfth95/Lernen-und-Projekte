"""Image supply-chain, database connect timeout and migration-configuration checks.

All offline: no database server, no Azure, no model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import event

from process_optimizer.persistence import cli as migrate_cli
from process_optimizer.persistence.database import (
    POSTGRES_CONNECT_TIMEOUT_SECONDS,
    Database,
)

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("agent", "workflow")


def _lock_pins(service: str) -> dict[str, str]:
    text = (ROOT / f"deploy/{service}/requirements.lock").read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", text, flags=re.MULTILINE))


# --------------------------------------------------------- PostgreSQL connect timeout


def _connect_parameters(database: Database) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    @event.listens_for(database.engine, "do_connect")
    def capture(dialect: Any, record: Any, args: Any, parameters: dict[str, Any]) -> None:
        seen.update(parameters)
        raise ConnectionAbortedError("captured; no connection is opened")

    with pytest.raises(Exception, match="captured"):
        database.engine.connect()
    return seen


def test_postgres_connections_time_out_after_ten_seconds() -> None:
    parameters = _connect_parameters(Database("postgresql+psycopg://synthetic@192.0.2.1/db"))

    assert POSTGRES_CONNECT_TIMEOUT_SECONDS == 10
    assert parameters["connect_timeout"] == 10


def test_explicit_url_timeout_is_kept_and_token_login_still_applies() -> None:
    database = Database(
        "postgresql+psycopg://synthetic@192.0.2.1/db?connect_timeout=3",
        access_token_provider=lambda: "synthetic-token",
        pool_pre_ping=True,
    )
    parameters = _connect_parameters(database)

    assert parameters["connect_timeout"] == "3"
    assert parameters["password"] == "synthetic-token"
    assert database.engine.pool._pre_ping is True  # noqa: SLF001 - semantics unchanged


def test_sqlite_receives_no_postgres_timeout(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'synthetic.sqlite3'}")
    with database.engine.connect():
        pass
    assert database.migration_status().current is False


# ------------------------------------------------------- migration configuration


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, "missing WORKFLOW_DATABASE_SCHEMA, missing WORKFLOW_RUNTIME_ROLE"),
        ({"WORKFLOW_DATABASE_SCHEMA": "process_optimizer"}, "missing WORKFLOW_RUNTIME_ROLE"),
        ({"WORKFLOW_RUNTIME_ROLE": "id-workflow"}, "missing WORKFLOW_DATABASE_SCHEMA"),
        (
            {"WORKFLOW_DATABASE_SCHEMA": "a;DROP", "WORKFLOW_RUNTIME_ROLE": "id-workflow"},
            "invalid PostgreSQL identifier in WORKFLOW_DATABASE_SCHEMA",
        ),
    ],
)
def test_incomplete_migration_configuration_is_refused_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
    expected: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("WORKFLOW_DATABASE_URL", "postgresql+psycopg://synthetic@192.0.2.1/db")
    for name in (migrate_cli.SCHEMA_ENV, migrate_cli.RUNTIME_ROLE_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not connect")

    monkeypatch.setattr(migrate_cli, "MigrationRunner", forbidden)

    assert migrate_cli.main(["--apply"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "refused", "reason": f"migration configuration: {expected}"}


def test_invalid_production_database_configuration_is_refused_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("WORKFLOW_PERSISTENCE_MODE", raising=False)
    monkeypatch.setenv(
        "WORKFLOW_DATABASE_URL", "postgresql+psycopg://synthetic:secret-value@192.0.2.1/db"
    )

    assert migrate_cli.main([]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "refused"
    assert "must not contain a password" in output
    assert "secret-value" not in output


# --------------------------------------------------------------- image supply chain


@pytest.mark.parametrize("service", SERVICES)
def test_runtime_locks_exclude_the_offline_ingestion_stack(service: str) -> None:
    pins = _lock_pins(service)

    for package in ("pm4py", "pillow", "matplotlib", "cvxopt", "scipy"):
        assert package not in pins
    # GHSA-6q48-6c7w-9gw2: certifi before 2026.10.18 trusts compromised emSign intermediates.
    assert tuple(int(part) for part in pins["certifi"].split(".")) >= (2026, 10, 18)
    assert "psycopg" in pins and "pyarrow" in pins and "pandas" in pins


def test_both_images_use_the_same_digest_pinned_base() -> None:
    bases = {
        re.search(r"^ARG PYTHON_BASE_IMAGE=(\S+)$", text, flags=re.MULTILINE).group(1)
        for text in (
            (ROOT / f"deploy/{service}/Dockerfile").read_text(encoding="utf-8")
            for service in SERVICES
        )
    }
    assert len(bases) == 1
    assert re.fullmatch(r"python:3\.12\.\d+-slim-bookworm@sha256:[0-9a-f]{64}", bases.pop())


def test_ingestion_extra_keeps_pm4py_for_development() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]

    assert "pm4py" not in dependencies
    assert re.search(r'ingestion = \[\s*"pm4py', pyproject)
    assert '"azure-it-service-process-optimizer[ingestion]"' in pyproject


def test_sdk_self_telemetry_is_off_and_content_capture_stays_off() -> None:
    env = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))["services"][
        "process-optimizer-agent"
    ]["env"]
    terraform = (ROOT / "infra/terraform/main.tf").read_text(encoding="utf-8")

    assert env["APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL"] == "true"
    assert env["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "false"
    assert env["ENABLE_AZURE_MONITOR"] == "true"
    assert re.search(
        r'name\s*=\s*"APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL"\s*value\s*=\s*"true"', terraform
    )
    assert re.search(r'name\s*=\s*"ENABLE_AZURE_MONITOR"\s*value\s*=\s*"true"', terraform)
