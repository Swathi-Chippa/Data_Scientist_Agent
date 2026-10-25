"""Pydantic contracts shared across the backend pipeline.

Field-for-field these follow ``spec/Interfaces.md``. The models are frozen and
reject unknown fields so a step cannot quietly widen a contract that downstream
steps, the trace, or the evaluator depend on.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GuardReason = Literal["write_attempt", "injection", "unsafe"]
ExecutionStatus = Literal["ok", "error", "timeout", "truncated"]
ExecutionErrorKind = Literal[
    "binder", "catalog", "syntax", "permission", "timeout", "other"
]
ColumnRole = Literal["dimension", "measure", "time", "key"]
ValidationMode = Literal["benchmark", "enterprise"]
ValidationCheckStatus = Literal["pass", "fail", "skipped"]
ValidationCheckName = Literal[
    "parse",
    "single_select",
    "no_dml_ddl",
    "allowlist",
    "approved_joins_fanout",
    "limits_bounds",
    "cost_gate",
    "readonly_exec",
    "post_exec",
    "reconcile",
]


class ContractModel(BaseModel):
    """Base for every contract object: immutable, strict about its shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class GatewayConfig(ContractModel):
    """Step 9 executor configuration for one datasource.

    ``read_only`` is deliberately not configurable: the gateway must never be
    able to open a writable connection.
    """

    datasource_id: str = Field(min_length=1)
    dialect: str = Field(min_length=1)
    connection_url: str = Field(min_length=1, repr=False)
    connection_pool_size: int = Field(default=5, ge=1)
    timeout_ms: int = Field(default=30_000, ge=1)
    read_only: bool = True

    @field_validator("read_only")
    @classmethod
    def _read_only_must_stay_locked(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("GatewayConfig.read_only is locked to True")
        return True


class ExecutionColumn(ContractModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    role: ColumnRole | None = None


class ExecutionError(ContractModel):
    kind: ExecutionErrorKind
    message: str = Field(min_length=1)


class ExecutionResult(ContractModel):
    """Step 9 output. Rows are tuples so a consumer cannot mutate the payload."""

    query_id: str = Field(min_length=1)
    datasource_id: str | None = None
    status: ExecutionStatus
    columns: tuple[ExecutionColumn, ...] = ()
    row_count: int = Field(ge=0)
    truncated: bool = False
    rows: tuple[tuple[Any, ...], ...] | None = None
    result_ref: str | None = None
    rows_scanned: int | None = Field(default=None, ge=0)
    duration_ms: int = Field(ge=0)
    error: ExecutionError | None = None

    @model_validator(mode="after")
    def _truncated_status_matches_flag(self) -> ExecutionResult:
        if self.status == "truncated" and not self.truncated:
            raise ValueError("status 'truncated' requires truncated=True")
        return self


class GuardResult(ContractModel):
    """Step 1 output. A passed guard cannot also report why it blocked."""

    passed: bool
    reason: GuardReason | None = None
    matched_rule: str | None = None

    @model_validator(mode="after")
    def _passed_guard_has_no_block_reason(self) -> GuardResult:
        if self.passed and self.reason is not None:
            raise ValueError("a passed GuardResult cannot carry a block reason")
        return self


class ValidationCheck(ContractModel):
    id: int = Field(ge=1, le=10)
    name: ValidationCheckName
    status: ValidationCheckStatus
    reason: str | None = None
    offending: tuple[str, ...] | None = None


class EstimatedCost(ContractModel):
    rows_scanned: int | None = Field(default=None, ge=0)
    bytes: int | None = Field(default=None, ge=0)
    plan_cost: float | None = Field(default=None, ge=0)


class ValidationResult(ContractModel):
    """Step 8 output; checks 8-10 are appended after execution."""

    approved: bool
    validation_mode: ValidationMode
    checks: tuple[ValidationCheck, ...] = ()
    est_cost: EstimatedCost | None = None
    rewrite: str | None = None

    @model_validator(mode="after")
    def _checks_are_consistent(self) -> ValidationResult:
        check_ids = [check.id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("validation check ids must be unique")
        if self.approved and any(check.status == "fail" for check in self.checks):
            raise ValueError("an approved ValidationResult cannot contain a failed check")
        return self


__all__ = [
    "ColumnRole",
    "ContractModel",
    "EstimatedCost",
    "ExecutionColumn",
    "ExecutionError",
    "ExecutionErrorKind",
    "ExecutionResult",
    "ExecutionStatus",
    "GatewayConfig",
    "GuardReason",
    "GuardResult",
    "ValidationCheck",
    "ValidationCheckName",
    "ValidationCheckStatus",
    "ValidationMode",
    "ValidationResult",
]
