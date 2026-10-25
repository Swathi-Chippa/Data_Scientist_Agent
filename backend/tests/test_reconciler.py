"""Tests for post-execution reconciliation checks (SQL-10, MET-5)."""

from __future__ import annotations

from contracts.models import (
    ExecutionColumn,
    ExecutionResult,
    MetricJoinPath,
    ModelUsage,
)
from models.client import ChatResponse
from sql.reconciler import Reconciler


def make_execution(
    rows: tuple[tuple[object, ...], ...],
    *,
    columns: tuple[tuple[str, str], ...] = (
        ("order_id", "text"),
        ("amount", "number"),
    ),
) -> ExecutionResult:
    return ExecutionResult(
        query_id="Q1",
        status="ok",
        columns=tuple(
            ExecutionColumn(name=name, type=data_type)
            for name, data_type in columns
        ),
        row_count=len(rows),
        rows=rows,
        duration_ms=5,
    )


def test_duplicate_primary_key_is_flagged():
    result = Reconciler().reconcile(
        make_execution((("o1", 10.0), ("o1", 12.0), ("o2", 7.0))),
        expected_grain=("order_id",),
    )

    assert result.status == "flags"
    assert any(
        check.name == "duplicates" and check.status == "flag"
        for check in result.checks
    )
    assert any("duplicate" in caveat.lower() for caveat in result.caveats)


def test_one_to_many_join_fanout_is_flagged():
    result = Reconciler().reconcile(
        make_execution((("o1", 10.0), ("o1", 12.0), ("o2", 7.0))),
        expected_grain=("order_id",),
        join_path=(
            MetricJoinPath(
                edge_id="order_payments.order_id=orders.order_id",
                cardinality="1:N",
            ),
        ),
    )

    assert result.status == "flags"
    assert any(
        check.name == "grain" and check.status == "flag"
        for check in result.checks
    )
    assert any("fan-out" in caveat.lower() for caveat in result.caveats)


def test_null_spike_on_mandatory_column_is_flagged():
    result = Reconciler().reconcile(
        make_execution((("o1", None), ("o2", 12.0))),
        expected_grain=("order_id",),
        mandatory_columns=("amount",),
    )

    assert result.status == "flags"
    assert any(
        check.name == "nulls" and check.status == "flag"
        for check in result.checks
    )
    assert any("null" in caveat.lower() for caveat in result.caveats)


def test_registry_total_mismatch_is_flagged_and_marks_retry():
    result = Reconciler().reconcile(
        make_execution((("o1", 10.0), ("o2", 12.0))),
        expected_grain=("order_id",),
        metric_id="revenue",
        observed_value=22.0,
        registry_expected=25.0,
        tolerance=0.5,
        retry_used=True,
    )

    assert result.status == "flags"
    assert result.registry_comparison is not None
    assert result.registry_comparison.within is False
    assert result.retry_used is True
    assert any(
        check.name == "registry_total" and check.status == "flag"
        for check in result.checks
    )
    assert any("tolerance" in caveat.lower() for caveat in result.caveats)


def test_out_of_range_values_are_flagged():
    result = Reconciler().reconcile(
        make_execution((("o1", -3.0), ("o2", 12.0))),
        expected_grain=("order_id",),
        value_ranges={"amount": (0.0, None)},
    )

    assert result.status == "flags"
    assert any(
        check.name == "ranges" and check.status == "flag"
        for check in result.checks
    )
    assert any("range" in caveat.lower() for caveat in result.caveats)


def test_clean_result_passes_all_checks():
    result = Reconciler().reconcile(
        make_execution((("o1", 10.0), ("o2", 12.0))),
        expected_grain=("order_id",),
        mandatory_columns=("amount",),
        metric_id="revenue",
        observed_value=22.0,
        registry_expected=22.2,
        tolerance=0.5,
    )

    assert result.status == "pass"
    assert result.caveats == ()
    assert all(check.status == "pass" for check in result.checks)


class FakeDiagnosisClient:
    def __init__(self) -> None:
        self.messages = None

    def chat(self, messages, **kwargs) -> ChatResponse:  # noqa: ANN001, ANN003
        self.messages = messages
        return ChatResponse(
            content="The duplicate order_id values indicate a one-to-many join fan-out.",
            model="deepseek-r1:7b",
            usage=ModelUsage(
                name="deepseek-r1:7b",
                version="deepseek-r1:7b",
                tokens_in=120,
                tokens_out=20,
            ),
        )


def test_reconciliation_diagnosis_uses_model_on_flags_only():
    reconciler = Reconciler()
    flagged = reconciler.reconcile(
        make_execution((("o1", 10.0), ("o1", 12.0))),
        expected_grain=("order_id",),
        join_path=(
            MetricJoinPath(
                edge_id="order_payments.order_id=orders.order_id",
                cardinality="1:N",
            ),
        ),
    )
    client = FakeDiagnosisClient()

    diagnosis = reconciler.diagnose(
        flagged,
        "What was revenue by order?",
        client=client,
    )

    assert diagnosis is not None
    assert "one-to-many" in diagnosis
    assert client.messages is not None

    clean = reconciler.reconcile(
        make_execution((("o1", 10.0), ("o2", 12.0))),
        expected_grain=("order_id",),
    )
    assert reconciler.diagnose(clean, "What was revenue?", client=client) is None
