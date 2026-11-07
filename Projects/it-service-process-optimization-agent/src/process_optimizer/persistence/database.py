"""Database abstraction and explicit migration runner."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from process_optimizer.persistence.migrations import (
    LATEST_VERSION,
    MigrationRunner,
    MigrationStatusV1,
)

# Kept for existing callers; it now names the latest migration, not only 0001.
INITIAL_SCHEMA_VERSION = LATEST_VERSION

# libpq waits indefinitely by default; a stopped or unreachable server must fail fast.
# Only the TCP connect is bounded: no statement timeout and no retry is added.
POSTGRES_CONNECT_TIMEOUT_SECONDS = 10


class Database:
    """SQLAlchemy unit-of-work boundary; SQLite is only the local adapter."""

    def __init__(
        self,
        url: str,
        *,
        access_token_provider: Callable[[], str] | None = None,
        pool_pre_ping: bool = False,
    ) -> None:
        connect_args: dict[str, object] = (
            {"check_same_thread": False} if url.startswith("sqlite") else {}
        )
        if url.startswith("postgresql") and "connect_timeout=" not in url:
            connect_args["connect_timeout"] = POSTGRES_CONNECT_TIMEOUT_SECONDS
        self.engine: Engine = create_engine(
            url,
            future=True,
            connect_args=connect_args,
            pool_pre_ping=pool_pre_ping,
        )
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        if access_token_provider is not None:
            event.listen(
                self.engine,
                "do_connect",
                self._managed_identity_connect_listener(access_token_provider),
            )
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _managed_identity_connect_listener(
        token_provider: Callable[[], str],
    ) -> Callable[..., None]:
        def provide_token(
            dialect: object,
            connection_record: object,
            connection_args: list[object],
            connection_parameters: dict[str, object],
        ) -> None:
            del dialect, connection_record, connection_args
            connection_parameters["password"] = token_provider()

        return provide_token

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def migrate(self) -> tuple[str, ...]:
        """Apply pending migrations. Only the migration entry point and local or
        ephemeral demo databases may call this; the durable runtime never does."""
        return MigrationRunner(self.engine).apply()

    def migration_status(self) -> MigrationStatusV1:
        """Read-only migration status; creates nothing."""
        return MigrationRunner(self.engine).status()

    def is_migrated(self) -> bool:
        return self.migration_status().current

    def require_migrated(self) -> None:
        """Fail closed when a runtime without DDL rights meets an unmigrated database."""
        MigrationRunner(self.engine).verify()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Provide an atomic SQLAlchemy transaction."""
        with self._sessions.begin() as session:
            yield session

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Provide a read-oriented session."""
        with self._sessions() as session:
            yield session

    def table_names(self) -> set[str]:
        return set(inspect(self.engine).get_table_names())
