"""Tests for the capped self-repair loop (SQL-11, SQL-13)."""

import sqlite3
from pathlib import Path

import pytest

from contracts.models import (
    AllowlistedColumn,
    ExecutionColumn,
    ExecutionError,
    ExecutionResult,
    GatewayConfig,
    SchemaAllowlist,
    SqlCandidate,
)
from gateway.connection import ConnectionManager
from gateway.executor import QueryExecutor
from schema_retriever.catalog import CatalogColumn, CatalogTable, SchemaCatalog
from sql.repairer import SelfRepairer
from sql.validator import SQLValidator


def sample_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        version="repair-test",
        tables=(
            CatalogTable(
                name="orders",
                columns=(
                    CatalogColumn(name="order_id", data_type="TEXT"),
                    CatalogColumn(
                        name="order_status",
                        data_type="TEXT",
                        values=("delivered", "shipped"),
                    ),
                    CatalogColumn(name="payment_value", data_type="REAL"),
                ),
            ),
        ),
    )


def sample_allowlist() -> SchemaAllowlist:
    return SchemaAllowlist(
        tables=("orders",),
        columns=(
            AllowlistedColumn(table="orders", column="order_id"),
            AllowlistedColumn(table="orders", column="order_status"),
            AllowlistedColumn(table="orders", column="payment_value"),
        ),
    )


def candidate(sql: str, *, candidate_id: str = "c1") -> SqlCandidate:
    return SqlCandidate(
        candidate_id=candidate_id,
        sql=sql,
        dialect="sqlite",
        origin="generator",
        intended_columns=(),
        grain=(),
        uses_edges=(),
    )


@pytest.fixture()
def executor(tmp_path: Path):
    database = tmp_path / "repair.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                order_status TEXT NOT NULL,
                payment_value REAL NOT NULL
            );
            INSERT INTO orders VALUES
                ('o1', 'delivered', 10.0),
                ('o2', 'shipped', 20.0);
            """
        )
        connection.commit()
    finally:
        connection.close()

    manager = ConnectionManager(
        GatewayConfig(
            datasource_id="repair-test",
            dialect="sqlite",
            connection_url=str(database),
            connection_pool_size=1,
            timeout_ms=5_000,
        )
    )
    try:
        yield QueryExecutor(manager, max_rows=10, timeout_ms=5_000)
    finally:
        manager.dispose()


def repairer(executor, *, reflector=None, allowlist=None):
    allowlist = allowlist or sample_allowlist()
    validator = SQLValidator(
        allowlist.tables,
        dialect="sqlite",
        mode="enterprise",
        columns=[
            f"{column.table}.{column.column}"
            for column in allowlist.columns
        ],
    )
    return SelfRepairer(
        validator=validator,
        executor=executor,
        catalog=sample_catalog(),
        reflector=reflector,
    )


def test_repairs_an_unknown_column_then_executes():
    from sql.repairer import SQLReflector

    class _Executor:
        def __init__(self):
            self.calls = 0

        def execute(self, sql, *, query_id="Q1"):
            self.calls += 1
            return ExecutionResult(
                query_id=query_id,
                status="ok",
                columns=(ExecutionColumn(name="order_status", type="text"),),
                row_count=1,
                rows=(("delivered",),),
                duration_ms=1,
            )

    executor = _Executor()
    outcome = repairer(executor).run(
        candidate("SELECT order_states FROM orders LIMIT 1"),
        allowlist=sample_allowlist(),
        question="How many delivered orders?",
        query_id="Q1",
    )

    assert outcome.success is True
    assert outcome.candidate is not None
    assert "order_status" in outcome.candidate.sql
    assert "order_states" not in outcome.candidate.sql
    assert outcome.candidate.origin == "repair"
    assert outcome.candidate.repair_of == "c1"
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].validation.approved is True
    assert executor.calls == 1


def test_repairs_a_syntax_error_then_executes(executor):
    outcome = repairer(executor).run(
        candidate("SELCT order_status FROM orders LIMIT 1"),
        allowlist=sample_allowlist(),
        question="Show order statuses.",
        query_id="Q1",
    )

    assert outcome.success is True
    assert outcome.execution is not None
    assert outcome.execution.status == "ok"
    assert len(outcome.attempts) == 1
    assert outcome.candidate.sql.upper().startswith("SELECT")


def test_successful_candidate_skips_repair(executor):
    outcome = repairer(executor).run(
        candidate("SELECT order_status FROM orders LIMIT 10"),
        allowlist=sample_allowlist(),
        question="Show order statuses.",
        query_id="Q1",
    )

    assert outcome.success is True
    assert outcome.attempts == ()
    assert outcome.execution is not None
    assert outcome.execution.row_count == 2


def test_runtime_error_is_repaired_with_the_frozen_allowlist():
    class _ErrorThenOkExecutor:
        def __init__(self):
            self.calls = 0

        def execute(self, sql, *, query_id="Q1"):
            self.calls += 1
            if self.calls == 1:
                return ExecutionResult(
                    query_id=query_id,
                    status="error",
                    row_count=0,
                    rows=None,
                    duration_ms=1,
                    error=ExecutionError(
                        kind="binder", message="no such column: order_status"
                    ),
                )
            return ExecutionResult(
                query_id=query_id,
                status="ok",
                columns=(ExecutionColumn(name="status", type="text"),),
                row_count=1,
                rows=(("delivered",),),
                duration_ms=1,
            )

    class _Reflector:
        def __init__(self):
            self.requests = []

        def reflect(self, request):
            self.requests.append(request)
            return SqlCandidate(
                candidate_id="repair-1",
                sql="SELECT order_status AS status FROM orders LIMIT 1",
                dialect="sqlite",
                origin="repair",
                intended_columns=(),
                grain=(),
                uses_edges=(),
                repair_of="c1",
            )

    reflected = _Reflector()
    executor = _ErrorThenOkExecutor()
    outcome = repairer(executor, reflector=reflected).run(
        candidate("SELECT order_status FROM orders LIMIT 1"),
        allowlist=sample_allowlist(),
        question="Show order statuses.",
        query_id="Q1",
    )

    assert outcome.success is True
    assert executor.calls == 2
    assert len(outcome.attempts) == 1
    assert reflected.requests[0].error.kind == "runtime"
    assert reflected.requests[0].allowlist == sample_allowlist()


def test_caps_repairs_at_two_and_returns_structured_clarification(executor):
    class _AlwaysInvalidReflector:
        def __init__(self):
            self.requests = []

        def reflect(self, request):
            self.requests.append(request)
            return candidate(
                "SELECT secret FROM orders LIMIT 1",
                candidate_id=f"repair-{request.attempt}",
            )

    reflected = _AlwaysInvalidReflector()
    outcome = repairer(executor, reflector=reflected).run(
        candidate("SELECT missing FROM orders LIMIT 1"),
        allowlist=sample_allowlist(),
        question="Show the secret orders.",
        query_id="Q1",
    )

    assert outcome.success is False
    assert outcome.clarification is not None
    assert outcome.clarification.what_understood
    assert outcome.clarification.what_needs_clarification
    assert len(outcome.attempts) == 2
    assert len(reflected.requests) == 2
    assert [request.attempt for request in reflected.requests] == [1, 2]
    assert all(
        request.allowlist == sample_allowlist()
        for request in reflected.requests
    )


def test_repairer_cannot_be_configured_above_two_attempts(executor):
    with pytest.raises(ValueError):
        SelfRepairer(
            validator=SQLValidator(("orders",), dialect="sqlite"),
            executor=executor,
            catalog=sample_catalog(),
            max_attempts=3,
        )
