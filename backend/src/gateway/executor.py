"""Read-only query execution with timeout and row-cap enforcement (SQL-9).

The executor never opens its own connection: engine ownership stays in
``connection.py``. SQLite queries are interrupted with the driver's progress
handler, so a timeout actually stops the statement rather than abandoning it.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from contracts.models import (
    ExecutionColumn,
    ExecutionError,
    ExecutionErrorKind,
    ExecutionResult,
)
from gateway.connection import ConnectionManager

_DEFAULT_MAX_ROWS = 1_000
_PROGRESS_STEPS = 1_000


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1_000))


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, (float, Decimal)):
        return "number"
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        return "text"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "blob"
    return "unknown"


def _classify_error(message: str) -> ExecutionErrorKind:
    lowered = message.lower()
    if (
        "readonly" in lowered
        or "read-only" in lowered
        or "not authorized" in lowered
        or "permission" in lowered
    ):
        return "permission"
    if "no such table" in lowered or "no such view" in lowered:
        return "catalog"
    if "no such column" in lowered:
        return "binder"
    if "syntax error" in lowered or "incomplete input" in lowered:
        return "syntax"
    if "interrupted" in lowered:
        return "timeout"
    return "other"


class QueryExecutor:
    """Executes one SQL statement against a managed read-only datasource."""

    def __init__(
        self,
        connections: ConnectionManager,
        *,
        max_rows: int = _DEFAULT_MAX_ROWS,
        timeout_ms: int | None = None,
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        if timeout_ms is not None and timeout_ms < 1:
            raise ValueError("timeout_ms must be at least 1")

        self._connections = connections
        self._max_rows = max_rows
        self._timeout_ms = (
            connections.timeout_ms if timeout_ms is None else timeout_ms
        )

    @property
    def connections(self) -> ConnectionManager:
        return self._connections

    @property
    def max_rows(self) -> int:
        return self._max_rows

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    def execute(self, sql: str, *, query_id: str = "Q1") -> ExecutionResult:
        started = time.monotonic()
        if not sql.strip():
            return self._error_result(
                query_id=query_id,
                started=started,
                kind="syntax",
                message="SQL is empty",
            )

        deadline = started + (self._timeout_ms / 1_000)
        timed_out = False
        rows: tuple[tuple[Any, ...], ...] = ()
        columns: tuple[ExecutionColumn, ...] = ()
        truncated = False

        try:
            with self._connections.connect() as connection:
                raw = connection.connection.driver_connection
                supports_progress = isinstance(raw, sqlite3.Connection)

                if supports_progress:

                    def _on_progress() -> int:
                        nonlocal timed_out
                        if time.monotonic() >= deadline:
                            timed_out = True
                            return 1
                        return 0

                    raw.set_progress_handler(_on_progress, _PROGRESS_STEPS)

                try:
                    cursor_result = connection.execute(text(sql))
                    keys = tuple(str(key) for key in cursor_result.keys())
                    fetched = cursor_result.fetchmany(self._max_rows + 1)
                finally:
                    if supports_progress:
                        try:
                            raw.set_progress_handler(None, 0)
                        except sqlite3.Error:
                            pass

        except OperationalError as exc:
            message = _first_line(getattr(exc, "orig", exc))
            if timed_out or "interrupted" in message.lower():
                return self._error_result(
                    query_id=query_id,
                    started=started,
                    kind="timeout",
                    message=f"query exceeded {self._timeout_ms} ms timeout",
                )
            return self._error_result(
                query_id=query_id,
                started=started,
                kind=_classify_error(message),
                message=message,
            )
        except SQLAlchemyError as exc:
            return self._error_result(
                query_id=query_id,
                started=started,
                kind=_classify_error(str(exc)),
                message=_first_line(exc),
            )

        truncated = len(fetched) > self._max_rows
        returned = fetched[: self._max_rows]
        rows = tuple(tuple(row) for row in returned)
        columns = tuple(
            ExecutionColumn(
                name=key,
                type=_infer_type(
                    rows[0][index] if rows and index < len(rows[0]) else None
                ),
            )
            for index, key in enumerate(keys)
        )

        return ExecutionResult(
            query_id=query_id,
            datasource_id=self._connections.datasource_id,
            status="truncated" if truncated else "ok",
            columns=columns,
            row_count=len(rows),
            truncated=truncated,
            rows=rows,
            duration_ms=_duration_ms(started),
        )

    def _error_result(
        self,
        *,
        query_id: str,
        started: float,
        kind: ExecutionErrorKind,
        message: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            query_id=query_id,
            datasource_id=self._connections.datasource_id,
            status="timeout" if kind == "timeout" else "error",
            columns=(),
            row_count=0,
            truncated=False,
            rows=None,
            duration_ms=_duration_ms(started),
            error=ExecutionError(kind=kind, message=message or "query failed"),
        )


def _first_line(error: object) -> str:
    text_value = str(error).strip()
    return text_value.splitlines()[0][:1_000] if text_value else "query failed"


__all__ = ["QueryExecutor"]
