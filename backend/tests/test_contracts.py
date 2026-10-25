"""Contract tests for the backend's Pydantic models.

These mirror the shapes in ``spec/Interfaces.md`` (sections 3.1, 3.8, 3.9)
and pin down the safety invariants the rest of the pipeline relies on.
"""

import pytest
from pydantic import ValidationError

from contracts.models import (
    ExecutionResult,
    GatewayConfig,
    GuardResult,
    ValidationCheck,
    ValidationResult,
)

CONNECTION_URL = "postgresql://analyst:hunter2@warehouse.internal:5432/retail"


def gateway_config(**overrides):
    values = {
        "datasource_id": "warehouse-main",
        "dialect": "postgres",
        "connection_url": CONNECTION_URL,
    }
    values.update(overrides)
    return GatewayConfig(**values)


class TestGatewayConfig:
    def test_holds_datasource_dialect_and_connection_url(self):
        config = gateway_config()

        assert config.datasource_id == "warehouse-main"
        assert config.dialect == "postgres"
        assert config.connection_url == CONNECTION_URL

    def test_read_only_is_locked_on_by_default(self):
        assert gateway_config().read_only is True

    def test_rejects_an_explicitly_writable_connection(self):
        with pytest.raises(ValidationError):
            gateway_config(read_only=False)

    def test_cannot_be_mutated_after_construction(self):
        config = gateway_config()

        with pytest.raises(ValidationError):
            config.read_only = False
        with pytest.raises(ValidationError):
            config.datasource_id = "warehouse-other"

    def test_does_not_leak_credentials_in_repr(self):
        assert "hunter2" not in repr(gateway_config())

    def test_rejects_unknown_fields(self):
        with pytest.raises(ValidationError):
            gateway_config(password="hunter2")

    def test_requires_positive_pool_size_and_timeout(self):
        with pytest.raises(ValidationError):
            gateway_config(connection_pool_size=0)
        with pytest.raises(ValidationError):
            gateway_config(timeout_ms=0)


def execution_result(**overrides):
    values = {
        "query_id": "Q1",
        "status": "ok",
        "columns": [
            {"name": "region", "type": "text", "role": "dimension"},
            {"name": "revenue", "type": "double", "role": "measure"},
        ],
        "row_count": 2,
        "rows": [["north", 120.5], ["south", 98.0]],
        "duration_ms": 42,
    }
    values.update(overrides)
    return ExecutionResult(**values)


class TestExecutionResult:
    def test_captures_a_successful_run(self):
        result = execution_result()

        assert result.query_id == "Q1"
        assert result.status == "ok"
        assert result.row_count == 2
        assert result.rows == (("north", 120.5), ("south", 98.0))
        assert result.duration_ms == 42
        assert result.truncated is False
        assert result.error is None

    @pytest.mark.parametrize("status", ["ok", "error", "timeout", "truncated"])
    def test_accepts_the_documented_statuses(self, status):
        result = execution_result(status=status, truncated=status == "truncated")

        assert result.status == status

    def test_rejects_an_unknown_status(self):
        with pytest.raises(ValidationError):
            execution_result(status="partial")

    def test_rejects_negative_row_count_or_duration(self):
        with pytest.raises(ValidationError):
            execution_result(row_count=-1)
        with pytest.raises(ValidationError):
            execution_result(duration_ms=-1)

    def test_truncated_status_requires_the_truncated_flag(self):
        with pytest.raises(ValidationError):
            execution_result(status="truncated", truncated=False)

    def test_columns_and_rows_are_immutable(self):
        result = execution_result()

        assert isinstance(result.columns, tuple)
        assert isinstance(result.rows, tuple)
        assert all(isinstance(row, tuple) for row in result.rows)
        with pytest.raises(ValidationError):
            result.row_count = 99

    def test_carries_structured_error_details(self):
        result = execution_result(
            status="error",
            rows=[],
            row_count=0,
            error={"kind": "permission", "message": "relation denied"},
        )

        assert result.error is not None
        assert result.error.kind == "permission"
        assert result.error.message == "relation denied"

    def test_rejects_an_unknown_error_kind(self):
        with pytest.raises(ValidationError):
            execution_result(error={"kind": "exploded", "message": "nope"})

    def test_rejects_unknown_column_roles(self):
        with pytest.raises(ValidationError):
            execution_result(columns=[{"name": "x", "type": "int", "role": "mystery"}])


class TestGuardResult:
    def test_records_a_passed_guard(self):
        result = GuardResult(passed=True)

        assert result.passed is True
        assert result.reason is None
        assert result.matched_rule is None

    def test_records_why_a_guard_blocked_the_question(self):
        result = GuardResult(
            passed=False, reason="write_attempt", matched_rule="guard.no-dml"
        )

        assert result.passed is False
        assert result.reason == "write_attempt"
        assert result.matched_rule == "guard.no-dml"

    def test_rejects_an_unknown_reason(self):
        with pytest.raises(ValidationError):
            GuardResult(passed=False, reason="vibes")

    def test_a_passed_guard_cannot_carry_a_block_reason(self):
        with pytest.raises(ValidationError):
            GuardResult(passed=True, reason="injection")

    def test_is_immutable(self):
        result = GuardResult(passed=True)

        with pytest.raises(ValidationError):
            result.passed = False


def validation_check(**overrides):
    values = {"id": 1, "name": "parse", "status": "pass"}
    values.update(overrides)
    return ValidationCheck(**values)


def validation_result(**overrides):
    values = {
        "approved": True,
        "validation_mode": "enterprise",
        "checks": [validation_check()],
    }
    values.update(overrides)
    return ValidationResult(**values)


class TestValidationResult:
    def test_captures_approved_checks(self):
        result = validation_result()

        assert result.approved is True
        assert result.validation_mode == "enterprise"
        assert result.checks == (validation_check(),)
        assert result.est_cost is None
        assert result.rewrite is None

    def test_rejects_an_unknown_validation_mode(self):
        with pytest.raises(ValidationError):
            validation_result(validation_mode="production")

    def test_rejects_an_unknown_check_name(self):
        with pytest.raises(ValidationError):
            validation_check(name="vibes")

    @pytest.mark.parametrize("check_id", [0, 11])
    def test_check_ids_stay_within_the_documented_range(self, check_id):
        with pytest.raises(ValidationError):
            validation_check(id=check_id)

    def test_a_result_cannot_be_approved_with_a_failing_check(self):
        with pytest.raises(ValidationError):
            validation_result(
                approved=True,
                checks=[validation_check(status="fail", reason="SELECT * is banned")],
            )

    def test_records_failures_and_offending_identifiers(self):
        result = validation_result(
            approved=False,
            checks=[
                validation_check(
                    id=4,
                    name="allowlist",
                    status="fail",
                    reason="column outside allowlist",
                    offending=["customers.ssn"],
                )
            ],
        )

        assert result.approved is False
        assert result.checks[0].offending == ("customers.ssn",)

    def test_rejects_duplicate_check_ids(self):
        with pytest.raises(ValidationError):
            validation_result(
                approved=False,
                checks=[
                    validation_check(id=4, status="fail"),
                    validation_check(id=4, name="no_dml_ddl", status="fail"),
                ],
            )

    def test_checks_are_immutable(self):
        result = validation_result()

        assert isinstance(result.checks, tuple)
        with pytest.raises(ValidationError):
            result.approved = False
