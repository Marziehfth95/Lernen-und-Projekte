"""Versioned, idempotent, forward-only schema migrations for the workflow database.

Migrations run only through the explicit migration entry point (`process-optimizer-migrate
--apply`) or, for local and ephemeral demo databases, through `Database.migrate()`. The
durable production runtime never applies them; it calls `verify()` and refuses to start
when anything is pending or when the database is newer than the code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    Column,
    Connection,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    insert,
    inspect,
    select,
    text,
)

from process_optimizer.persistence.migrations import v0001_initial, v0002_policy_provenance


@dataclass(frozen=True)
class Migration:
    version: str
    description: str
    upgrade: Callable[[Connection], None]


# Append only. Never edit or reorder an entry that has been applied anywhere.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(v0001_initial.VERSION, v0001_initial.DESCRIPTION, v0001_initial.upgrade),
    Migration(
        v0002_policy_provenance.VERSION,
        v0002_policy_provenance.DESCRIPTION,
        v0002_policy_provenance.upgrade,
    ),
)
# Every table the migrations create, in creation order (the ledger excepted).
MIGRATED_TABLES = (*v0001_initial.TABLES, *v0002_policy_provenance.TABLES)
LATEST_VERSION = MIGRATIONS[-1].version

# Same structure as the original ORM table, so existing databases are never altered.
_ledger_metadata = MetaData()
schema_migrations = Table(
    "schema_migrations",
    _ledger_metadata,
    Column("version", String(32), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)

# Arbitrary constant: serialises concurrent migration jobs on PostgreSQL.
_ADVISORY_LOCK_KEY = 7_211_430_118


class MigrationStatusV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dialect: str
    latest_version: str
    applied_versions: tuple[str, ...]
    pending_versions: tuple[str, ...]
    unknown_versions: tuple[str, ...]

    @property
    def current(self) -> bool:
        return not self.pending_versions and not self.unknown_versions


class SchemaNotMigratedError(RuntimeError):
    """The database schema does not match the migrations this code expects."""


class MigrationRunner:
    """Apply, report or verify migrations against one engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def status(self) -> MigrationStatusV1:
        """Read-only: never creates the ledger table or any other object."""
        with self._engine.connect() as connection:
            applied = self._applied(connection)
        known = [item.version for item in MIGRATIONS]
        return MigrationStatusV1(
            dialect=self._engine.dialect.name,
            latest_version=LATEST_VERSION,
            applied_versions=tuple(sorted(applied)),
            pending_versions=tuple(version for version in known if version not in applied),
            unknown_versions=tuple(sorted(set(applied) - set(known))),
        )

    def verify(self) -> MigrationStatusV1:
        status = self.status()
        if status.unknown_versions:
            raise SchemaNotMigratedError(
                "Workflow database is newer than this code: " + ", ".join(status.unknown_versions)
            )
        if status.pending_versions:
            raise SchemaNotMigratedError(
                "Workflow database schema is not migrated: " + ", ".join(status.pending_versions)
            )
        return status

    def apply(self) -> tuple[str, ...]:
        """Apply pending migrations in order; each one is atomic with its ledger row."""
        applied_now: list[str] = []
        for migration in MIGRATIONS:
            with self._engine.begin() as connection:
                if connection.dialect.name == "postgresql":
                    # Held until commit: a concurrent job waits, then sees the ledger row.
                    connection.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
                    )
                schema_migrations.create(connection, checkfirst=True)
                if migration.version in self._applied(connection):
                    continue
                migration.upgrade(connection)
                connection.execute(
                    insert(schema_migrations).values(
                        version=migration.version, applied_at=datetime.now(UTC)
                    )
                )
                applied_now.append(migration.version)
        unknown = self.status().unknown_versions
        if unknown:
            raise SchemaNotMigratedError(
                "Workflow database is newer than this code: " + ", ".join(unknown)
            )
        return tuple(applied_now)

    @staticmethod
    def _applied(connection: Connection) -> set[str]:
        if not inspect(connection).has_table("schema_migrations"):
            return set()
        return set(connection.execute(select(schema_migrations.c.version)).scalars())
