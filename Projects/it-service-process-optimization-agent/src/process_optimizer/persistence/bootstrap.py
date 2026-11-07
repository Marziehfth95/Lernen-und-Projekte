"""PostgreSQL role, schema and grant bootstrap for the durable workflow database.

Python equivalent of `deploy/postgresql/bootstrap.sql.template`, which needs `psql`. That
tool is not available inside the private VNet. The work is split by owner:

1. `database` phase, run as the Entra administrator (`id-dbadmin`): map the migration and
   runtime managed identities to PostgreSQL roles, remove PUBLIC access, and allow only
   the migration role to create a schema.
2. `prepare_schema` and `grant_runtime`, run by the migration job (`id-migration`), which
   owns the schema and tables and can therefore grant on them.

The runtime role gets no DDL, no DELETE and no TRUNCATE. Append-only tables get
SELECT/INSERT, and only state tables also get UPDATE.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from sqlalchemy import Connection, text

from process_optimizer.persistence.cloud import database_from_environment

APPEND_ONLY_TABLES = (
    "recommendations",
    "approval_decisions",
    "audit_events",
    "approval_policy_provenance",
)
MUTABLE_STATE_TABLES = ("approval_requests", "improvement_actions", "notification_outbox")
LEDGER_TABLE = "schema_migrations"

_ROLE = re.compile(r"^[A-Za-z0-9_-]{1,63}$")
_OID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def is_valid_identifier(value: str) -> bool:
    """Schema and role names this module will quote; checked before any connection."""
    return _ROLE.fullmatch(value) is not None


def _identifier(connection: Connection, value: str) -> str:
    if not is_valid_identifier(value):
        raise ValueError("PostgreSQL identifiers must match [A-Za-z0-9_-]{1,63}")
    return connection.dialect.identifier_preparer.quote_identifier(value)


def map_principals(
    connection: Connection, principals: dict[str, str]
) -> None:
    """Bind each role to one exact managed-identity object ID (Azure-only extension)."""
    for name, object_id in principals.items():
        _identifier(connection, name)
        if not _OID.fullmatch(object_id):
            raise ValueError("Managed-identity object IDs must be UUIDs")
        exists = connection.execute(
            text("SELECT 1 FROM pg_catalog.pgaadauth_list_principals(false) WHERE rolename = :n"),
            {"n": name},
        ).first()
        if exists is None:
            connection.execute(
                text(
                    "SELECT * FROM pg_catalog.pgaadauth_create_principal_with_oid"
                    "(:n, :oid, 'service', false, false)"
                ),
                {"n": name, "oid": object_id},
            )


def secure_database(connection: Connection, *, runtime_role: str, migration_role: str) -> None:
    """Database-level privileges; idempotent."""
    name = connection.execute(text("SELECT current_database()")).scalar_one()
    database = _identifier(connection, name)
    runtime = _identifier(connection, runtime_role)
    migration = _identifier(connection, migration_role)
    connection.execute(text(f"REVOKE CONNECT ON DATABASE {database} FROM PUBLIC"))
    connection.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
    connection.execute(text(f"GRANT CONNECT ON DATABASE {database} TO {migration}, {runtime}"))
    connection.execute(text(f"GRANT CREATE ON DATABASE {database} TO {migration}"))


def prepare_schema(connection: Connection, *, schema: str, runtime_role: str) -> None:
    """Run as the migration role: create its own schema and let the runtime use it."""
    quoted = _identifier(connection, schema)
    runtime = _identifier(connection, runtime_role)
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted}"))
    connection.execute(text(f"REVOKE ALL ON SCHEMA {quoted} FROM PUBLIC"))
    connection.execute(text(f"GRANT USAGE ON SCHEMA {quoted} TO {runtime}"))


def grant_runtime(connection: Connection, *, schema: str, runtime_role: str) -> None:
    """Run as the migration role after migrating: least-privilege table grants."""
    runtime = _identifier(connection, runtime_role)

    def table(name: str) -> str:
        return f"{_identifier(connection, schema)}.{_identifier(connection, name)}"

    privileges = {
        **dict.fromkeys(APPEND_ONLY_TABLES, "SELECT, INSERT"),
        **dict.fromkeys(MUTABLE_STATE_TABLES, "SELECT, INSERT, UPDATE"),
        LEDGER_TABLE: "SELECT",
    }
    for name, granted in privileges.items():
        # Revoke first so a re-run converges to exactly these grants.
        connection.execute(text(f"REVOKE ALL ON TABLE {table(name)} FROM {runtime}"))
        connection.execute(text(f"GRANT {granted} ON TABLE {table(name)} TO {runtime}"))


def main(argv: list[str] | None = None) -> int:
    """`database` phase entry point for the Entra-administrator bootstrap job."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-principal-mapping",
        action="store_true",
        help="Roles already exist (local test clusters without pgaadauth).",
    )
    args = parser.parse_args(argv)
    runtime_role = os.environ["RUNTIME_PRINCIPAL_NAME"]
    migration_role = os.environ["MIGRATION_PRINCIPAL_NAME"]
    database = database_from_environment()
    try:
        with database.engine.begin() as connection:
            if connection.dialect.name != "postgresql":
                raise ValueError("Bootstrap applies to PostgreSQL only")
            if not args.skip_principal_mapping:
                map_principals(
                    connection,
                    {
                        runtime_role: os.environ["RUNTIME_PRINCIPAL_OBJECT_ID"],
                        migration_role: os.environ["MIGRATION_PRINCIPAL_OBJECT_ID"],
                    },
                )
            secure_database(connection, runtime_role=runtime_role, migration_role=migration_role)
    finally:
        database.engine.dispose()
    print(json.dumps({"phase": "database", "status": "applied"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
