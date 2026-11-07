"""Explicit database migration entry point; status-only unless --apply is supplied.

This is the only production path that changes the workflow schema. It runs as a separate
Container Apps job under the migration identity, never during application startup.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from process_optimizer.persistence.bootstrap import (
    grant_runtime,
    is_valid_identifier,
    prepare_schema,
)
from process_optimizer.persistence.cloud import database_from_environment, persistence_mode
from process_optimizer.persistence.migrations import MigrationRunner, SchemaNotMigratedError

APPLY_CONFIRMATION_ENV = "WORKFLOW_MIGRATION_APPLY"
SCHEMA_ENV = "WORKFLOW_DATABASE_SCHEMA"
RUNTIME_ROLE_ENV = "WORKFLOW_RUNTIME_ROLE"


def _refuse(reason: str) -> int:
    print(json.dumps({"status": "refused", "reason": reason}))
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply pending migrations. Omit to print a non-mutating status.",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="Exit non-zero unless every migration is applied and none is unknown.",
    )
    args = parser.parse_args(argv)
    durable = persistence_mode() == "durable_postgresql"
    if args.apply and durable and os.environ.get(APPLY_CONFIRMATION_ENV) != "true":
        # A second, job-only switch so an operator shell cannot migrate by accident.
        return _refuse(f"{APPLY_CONFIRMATION_ENV}!=true")
    try:
        database = database_from_environment()
    except ValueError as error:
        return _refuse(f"invalid database configuration: {error}")
    postgres = database.engine.dialect.name == "postgresql"
    if args.apply and postgres:
        # Checked before any connection is opened, so a misconfigured job changes nothing.
        missing = [name for name in (SCHEMA_ENV, RUNTIME_ROLE_ENV) if not os.environ.get(name)]
        invalid = [
            name
            for name in (SCHEMA_ENV, RUNTIME_ROLE_ENV)
            if name not in missing and not is_valid_identifier(os.environ[name])
        ]
        if missing or invalid:
            database.engine.dispose()
            problems = [f"missing {name}" for name in missing]
            problems += [f"invalid PostgreSQL identifier in {name}" for name in invalid]
            return _refuse("migration configuration: " + ", ".join(problems))
    runner = MigrationRunner(database.engine)
    result: dict[str, object] = {"persistence_mode": persistence_mode()}
    try:
        if args.apply:
            if postgres:
                # The migration role owns the schema; it creates it and, after the
                # migration, grants the runtime role its least-privilege access.
                schema = os.environ[SCHEMA_ENV]
                runtime_role = os.environ[RUNTIME_ROLE_ENV]
                with database.engine.begin() as connection:
                    prepare_schema(connection, schema=schema, runtime_role=runtime_role)
            result["applied_now"] = list(runner.apply())
            if postgres:
                with database.engine.begin() as connection:
                    grant_runtime(connection, schema=schema, runtime_role=runtime_role)
                result["runtime_grants"] = "applied"
            result["mode"] = "apply"
        elif args.verify:
            runner.verify()
            result["mode"] = "verify"
        else:
            result["mode"] = "status"
        status = runner.status()
    except SchemaNotMigratedError as error:
        result.update({"status": "not_current", "reason": str(error)})
        print(json.dumps(result, indent=2))
        return 1
    finally:
        database.engine.dispose()
    result.update(status.model_dump(mode="json"))
    result["status"] = "current" if status.current else "not_current"
    print(json.dumps(result, indent=2))
    return 0 if status.current or not (args.apply or args.verify) else 1


if __name__ == "__main__":
    sys.exit(main())
