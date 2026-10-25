"""Engine management for the read-only multi-database gateway (SAFE-2).

The manager owns one engine per datasource. SQLite is opened through a
``file:...?mode=ro`` URI so write attempts fail inside the driver, not in
application code.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, make_url
from sqlalchemy.engine import Connection, Engine

from contracts.models import GatewayConfig


class UnsupportedDialectError(ValueError):
    """Raised when a dialect has no read-only adapter yet."""


_SUPPORTED_DIALECTS = frozenset({"sqlite", "sqlite3"})


def _sqlite_database_path(connection_url: str) -> Path:
    raw = connection_url.strip()
    if not raw:
        raise ValueError("connection_url must not be empty")

    if "://" in raw:
        url = make_url(raw)
        if url.get_backend_name() != "sqlite":
            raise UnsupportedDialectError(
                f"dialect {url.get_backend_name()!r} is not supported yet"
            )
        database = url.database or ""
    else:
        database = raw

    if database.startswith("file:"):
        database = database[len("file:") :]
    database = database.split("?", 1)[0]

    if database in ("", ":memory:"):
        raise ValueError("the gateway requires a file-backed SQLite database")

    path = Path(database).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    return path


def _sqlite_read_only_url(connection_url: str) -> str:
    path = _sqlite_database_path(connection_url)
    return f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true"


class ConnectionManager:
    """Owns the read-only engine for one configured datasource."""

    def __init__(self, config: GatewayConfig) -> None:
        dialect = config.dialect.lower()
        if dialect not in _SUPPORTED_DIALECTS:
            raise UnsupportedDialectError(
                f"dialect {config.dialect!r} is not supported yet"
            )
        if config.read_only is not True:
            raise ValueError("ConnectionManager only supports read-only datasources")

        self._config = config
        self._read_only_url = _sqlite_read_only_url(config.connection_url)
        self._engine = create_engine(
            self._read_only_url,
            pool_size=config.connection_pool_size,
            connect_args={"timeout": config.timeout_ms / 1000.0},
        )

    @property
    def config(self) -> GatewayConfig:
        return self._config

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def datasource_id(self) -> str:
        return self._config.datasource_id

    @property
    def timeout_ms(self) -> int:
        return self._config.timeout_ms

    @property
    def dialect(self) -> str:
        return "sqlite"

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with self._engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only = ON")
            yield connection

    def dispose(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> ConnectionManager:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.dispose()


__all__ = ["ConnectionManager", "UnsupportedDialectError"]
