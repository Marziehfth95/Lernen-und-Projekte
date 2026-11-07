"""Versioned migration, durability and least-privilege tests.

The SQLite tests always run. The PostgreSQL tests run only when
PROCESS_OPTIMIZER_TEST_POSTGRES_ADMIN_URL points at an isolated, disposable local cluster
(superuser, e.g. `postgresql+psycopg://postgres@127.0.0.1:55432/postgres`). Each test creates
its own throwaway database and roles. They never touch Azure or the repository databases.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateIndex, CreateTable
from tests.test_workflow import (  # noqa: F401 - pytest fixture re-export
    ANALYST,
    OWNER,
    decision,
    evidence,
    submission,
)

from process_optimizer.persistence import Database
from process_optimizer.persistence import cli as migrate_cli
from process_optimizer.persistence.bootstrap import (
    APPEND_ONLY_TABLES,
    LEDGER_TABLE,
    MUTABLE_STATE_TABLES,
    grant_runtime,
    prepare_schema,
    secure_database,
)
from process_optimizer.persistence.migrations import (
    LATEST_VERSION,
    MIGRATED_TABLES,
    MIGRATIONS,
    MigrationRunner,
    SchemaNotMigratedError,
    schema_migrations,
    v0001_initial,
    v0002_policy_provenance,
)
from process_optimizer.persistence.models import Base
from process_optimizer.workflow.service import WorkflowService
from process_optimizer.workflow.state import ApprovalState

ALL_VERSIONS = tuple(migration.version for migration in MIGRATIONS)
MIGRATED_NAMES = {table.name for table in MIGRATED_TABLES}

ADMIN_URL_ENV = "PROCESS_OPTIMIZER_TEST_POSTGRES_ADMIN_URL"


# ------------------------------------------------------------------ offline (SQLite)


def _ddl(metadata_tables: Any, dialect: Any) -> set[str]:
    statements = set()
    for table in metadata_tables:
        statements.add(str(CreateTable(table).compile(dialect=dialect)).strip())
        for index in table.indexes:
            statements.add(str(CreateIndex(index).compile(dialect=dialect)).strip())
    return statements


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_models_match_the_frozen_migrations(dialect: Any) -> None:
    # Any ORM change without a new migration fails here.
    models = [table for name, table in Base.metadata.tables.items() if name != LEDGER_TABLE]
    assert _ddl(models, dialect) == _ddl(MIGRATED_TABLES, dialect)
    assert _ddl([Base.metadata.tables[LEDGER_TABLE]], dialect) == _ddl(
        [schema_migrations], dialect
    )


def test_runtime_grant_lists_cover_every_migrated_table() -> None:
    assert set(APPEND_ONLY_TABLES) | set(MUTABLE_STATE_TABLES) == MIGRATED_NAMES
    # Provenance is evidence of what was accepted: the runtime may add it, never change it.
    assert "approval_policy_provenance" in APPEND_ONLY_TABLES
    assert not set(APPEND_ONLY_TABLES) & set(MUTABLE_STATE_TABLES)


def test_status_is_read_only_and_apply_is_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'm.sqlite3'}")
    runner = MigrationRunner(engine)

    before = runner.status()
    assert before.pending_versions == tuple(m.version for m in MIGRATIONS)
    assert _tables(engine) == set()  # status created nothing
    with pytest.raises(SchemaNotMigratedError, match="not migrated"):
        runner.verify()

    assert runner.apply() == ALL_VERSIONS
    assert runner.apply() == ()
    assert runner.verify().current
    assert _tables(engine) == MIGRATED_NAMES | {LEDGER_TABLE}


def test_0002_only_adds_the_provenance_table_to_a_0001_database(
    tmp_path: Path, evidence: Any  # noqa: F811
) -> None:
    # A 0001 database holding a pending approval, as it exists before this release
    # (built with this code, then reduced to 0001 in this throw-away test database).
    path = tmp_path / "v1.sqlite3"
    WorkflowService(Database(f"sqlite:///{path}"), evidence).submit(submission(), ANALYST)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE approval_policy_provenance"))
        connection.execute(text("DELETE FROM schema_migrations WHERE version = '0002'"))
    before = _snapshot(path)
    assert before["approval_requests"] and before["recommendations"]

    assert LATEST_VERSION == v0002_policy_provenance.VERSION
    assert MigrationRunner(engine).apply() == (v0002_policy_provenance.VERSION,)
    after = _snapshot(path)

    assert _tables(engine) == MIGRATED_NAMES | {LEDGER_TABLE}
    assert {name: rows for name, rows in after.items() if name in before} == {
        **before, LEDGER_TABLE: after[LEDGER_TABLE]
    }
    assert after["approval_policy_provenance"] == []
    assert MigrationRunner(engine).apply() == ()


def test_database_newer_than_code_is_refused(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'm.sqlite3'}")
    runner = MigrationRunner(engine)
    runner.apply()
    with engine.begin() as connection:
        connection.execute(insert(schema_migrations).values(version="9999", applied_at=_now()))

    with pytest.raises(SchemaNotMigratedError, match="newer than this code"):
        runner.verify()
    with pytest.raises(SchemaNotMigratedError, match="newer than this code"):
        runner.apply()


def test_databases_created_by_the_previous_create_all_need_no_change(
    tmp_path: Path, evidence: Any  # noqa: F811
) -> None:
    path = tmp_path / "legacy.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    # Exactly what the former create_all-based 0001 produced (the frozen 0001 tables).
    v0001_initial.metadata.create_all(engine)
    schema_migrations.create(engine)
    with engine.begin() as connection:
        connection.execute(insert(schema_migrations).values(version="0001", applied_at=_now()))
    WorkflowService(Database(f"sqlite:///{path}"), evidence).submit(submission(), ANALYST)
    snapshot = _snapshot(path)

    assert MigrationRunner(engine).apply() == ()
    WorkflowService(Database(f"sqlite:///{path}"), evidence, apply_migrations=False)
    assert _snapshot(path) == snapshot


def test_cli_status_and_verify_never_migrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "cli.sqlite3"
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("WORKFLOW_DATABASE_URL", f"sqlite:///{path}")

    assert migrate_cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "not_current"
    assert migrate_cli.main(["--verify"]) == 1
    capsys.readouterr()
    assert _tables(create_engine(f"sqlite:///{path}")) == set()

    assert migrate_cli.main(["--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["applied_now"] == list(ALL_VERSIONS)
    assert migrate_cli.main(["--verify"]) == 0


def test_cli_apply_against_durable_database_needs_the_job_switch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("WORKFLOW_PERSISTENCE_MODE", raising=False)
    monkeypatch.delenv(migrate_cli.APPLY_CONFIRMATION_ENV, raising=False)

    def forbidden(**kwargs: Any) -> Any:
        raise AssertionError("must not connect")

    monkeypatch.setattr(migrate_cli, "database_from_environment", forbidden)
    assert migrate_cli.main(["--apply"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_workflow_api_never_creates_the_durable_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence: Any  # noqa: F811
) -> None:
    from fastapi import HTTPException
    from starlette.requests import Request

    from process_optimizer.api import workflow as workflow_api

    path = tmp_path / "durable.sqlite3"
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (analysis / "evidence_contract.json").write_text(evidence.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("PROCESS_OPTIMIZER_ANALYSIS_DIR", str(analysis))
    monkeypatch.setenv("WORKFLOW_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setattr(workflow_api, "persistence_mode", lambda: "durable_postgresql")
    monkeypatch.setattr(
        workflow_api, "database_from_environment", lambda: Database(f"sqlite:///{path}")
    )
    request = Request({"type": "http", "app": type("App", (), {"state": type("S", (), {})()})()})

    with pytest.raises(HTTPException) as error:
        workflow_api._service(request)

    assert error.value.status_code == 503
    assert _tables(create_engine(f"sqlite:///{path}")) == set()


# ------------------------------------------------------------- PostgreSQL (isolated)


requires_postgres = pytest.mark.skipif(
    not os.environ.get(ADMIN_URL_ENV), reason=f"set {ADMIN_URL_ENV} to an isolated test cluster"
)
SCHEMA = "process_optimizer"


@pytest.fixture
def postgres() -> Iterator[dict[str, str]]:
    admin_url = make_url(os.environ[ADMIN_URL_ENV])
    suffix = uuid.uuid4().hex[:10]
    names = {
        "database": f"po_test_{suffix}",
        "migration": f"id-migration-{suffix}",
        "runtime": f"id-workflow-{suffix}",
    }
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{names["database"]}"'))
        for role in (names["migration"], names["runtime"]):
            connection.execute(text(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB'))
    database_admin = create_engine(admin_url.set(database=names["database"]))
    with database_admin.begin() as connection:
        secure_database(
            connection, runtime_role=names["runtime"], migration_role=names["migration"]
        )
    database_admin.dispose()

    def url(role: str) -> str:
        return admin_url.set(
            username=role,
            database=names["database"],
            query={"options": f"-csearch_path={SCHEMA}"},
        ).render_as_string(hide_password=False)

    names["migration_url"] = url(names["migration"])
    names["runtime_url"] = url(names["runtime"])
    try:
        yield names
    finally:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND pid <> pg_backend_pid()"
                ),
                {"d": names["database"]},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{names["database"]}"'))
            for role in (names["runtime"], names["migration"]):
                connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


def _migrate(names: dict[str, str]) -> tuple[str, ...]:
    engine = create_engine(names["migration_url"])
    try:
        with engine.begin() as connection:
            prepare_schema(connection, schema=SCHEMA, runtime_role=names["runtime"])
        applied = MigrationRunner(engine).apply()
        with engine.begin() as connection:
            grant_runtime(connection, schema=SCHEMA, runtime_role=names["runtime"])
        return applied
    finally:
        engine.dispose()


@requires_postgres
def test_postgres_runtime_refuses_unmigrated_schema(
    postgres: dict[str, str], evidence: Any  # noqa: F811
) -> None:
    with pytest.raises(SchemaNotMigratedError):
        WorkflowService(Database(postgres["runtime_url"]), evidence, apply_migrations=False)


@requires_postgres
def test_postgres_migration_is_versioned_idempotent_and_serialised(
    postgres: dict[str, str],
) -> None:
    engine = create_engine(postgres["migration_url"])
    with engine.begin() as connection:
        prepare_schema(connection, schema=SCHEMA, runtime_role=postgres["runtime"])
    runners = [MigrationRunner(create_engine(postgres["migration_url"])) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda runner: runner.apply(), runners))

    # The advisory lock lets exactly one concurrent job apply each migration.
    assert sorted(item for result in results for item in result) == list(ALL_VERSIONS)
    assert MigrationRunner(engine).apply() == ()
    status = MigrationRunner(engine).verify()
    assert status.dialect == "postgresql" and status.applied_versions == ALL_VERSIONS
    with engine.connect() as connection:
        tables = set(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = :s"), {"s": SCHEMA}
            ).scalars()
        )
    assert tables == MIGRATED_NAMES | {LEDGER_TABLE}
    engine.dispose()
    for runner in runners:
        runner._engine.dispose()


@requires_postgres
def test_postgres_workflow_records_are_durable_across_restart(
    postgres: dict[str, str], evidence: Any  # noqa: F811
) -> None:
    assert _migrate(postgres) == ALL_VERSIONS

    first = Database(postgres["runtime_url"])
    service = WorkflowService(first, evidence, apply_migrations=False)
    pending = service.submit(submission(), ANALYST)
    replay = service.submit(submission(), ANALYST)  # submission idempotency
    approved = service.decide(
        pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER
    )
    replayed_decision = service.decide(
        pending.approval_request_id, ApprovalState.APPROVED, decision(pending), OWNER
    )
    first.engine.dispose()  # simulate a replica restart

    restarted = WorkflowService(
        Database(postgres["runtime_url"]), evidence, apply_migrations=False
    )
    detail = restarted.get_approval(pending.approval_request_id)
    tickets = restarted.list_tickets()
    audit = [event.action_type for event in restarted.list_audit()]

    assert replay.approval_request_id == pending.approval_request_id
    assert approved.state == replayed_decision.state == ApprovalState.EXECUTED
    assert detail.recommendation_version_hash == pending.recommendation_version_hash
    assert detail.state == ApprovalState.EXECUTED
    assert len(tickets) == 1 and tickets[0].approval_reference
    assert {"RECOMMENDATION_SUBMITTED", "APPROVAL_GRANTED", "ACTION_EXECUTION_COMPLETED"} <= set(
        audit
    )
    restarted.database.engine.dispose()


@requires_postgres
def test_postgres_runtime_role_cannot_alter_schema_or_history(
    postgres: dict[str, str], evidence: Any  # noqa: F811
) -> None:
    _migrate(postgres)
    service = WorkflowService(Database(postgres["runtime_url"]), evidence, apply_migrations=False)
    service.submit(submission(), ANALYST)
    service.database.engine.dispose()

    engine = create_engine(postgres["runtime_url"])
    forbidden = (
        "CREATE TABLE process_optimizer.intruder (id int)",
        "DROP TABLE process_optimizer.audit_events",
        "ALTER TABLE process_optimizer.recommendations ADD COLUMN x int",
        "DELETE FROM process_optimizer.audit_events",
        "TRUNCATE process_optimizer.approval_requests",
        "UPDATE process_optimizer.recommendations SET payload_json = '{}'",
        "UPDATE process_optimizer.audit_events SET result = 'tampered'",
        "UPDATE process_optimizer.approval_policy_provenance SET policy_version = 'x'",
        "DELETE FROM process_optimizer.approval_policy_provenance",
        "ALTER TABLE process_optimizer.approval_policy_provenance DROP COLUMN policy_id",
        "INSERT INTO process_optimizer.schema_migrations VALUES ('9999', now())",
        "CREATE SCHEMA intruder",
    )
    for statement in forbidden:
        with engine.connect() as connection, pytest.raises(DBAPIError) as error:
            connection.execute(text(statement))
        # SQLSTATE 42501 = insufficient_privilege ("permission denied" / "must be owner").
        assert error.value.orig.sqlstate == "42501", statement
    engine.dispose()


# ------------------------------------------------------------------------ helpers


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _tables(engine: Any) -> set[str]:
    from sqlalchemy import inspect

    return set(inspect(engine).get_table_names())


def _snapshot(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master")]
        return {
            name: sorted(map(repr, connection.execute(f'SELECT * FROM "{name}"').fetchall()))
            for name in sorted(names)
            if not name.startswith("sqlite_autoindex")
            and connection.execute(
                "SELECT type FROM sqlite_master WHERE name = ?", (name,)
            ).fetchone()[0] == "table"
        }
    finally:
        connection.close()

