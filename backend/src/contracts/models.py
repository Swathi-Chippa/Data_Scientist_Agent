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
SqlOrigin = Literal["compiler", "generator", "repair"]
RepairErrorKind = Literal["parse", "validation", "runtime", "reconciliation"]
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


class AnswerabilityResult(ContractModel):
    """Step 5 output.

    ``confidence`` is confidence in the answerable/refused decision. A refusal
    must name what the catalog cannot answer and may list covered domains.
    """

    answerable: bool
    confidence: float = Field(ge=0.0, le=1.0)
    missing: tuple[str, ...] = ()
    closest_answerable: str | None = None
    cheap_filter_hit: str | None = None
    available_domains: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _refusal_names_the_gap(self) -> AnswerabilityResult:
        if not self.answerable and not self.missing:
            raise ValueError("a refused AnswerabilityResult must name what is missing")
        if self.answerable and self.missing:
            raise ValueError("an answerable result cannot list missing attributes")
        return self


class LinkedValue(ContractModel):
    """A question phrase grounded to one database literal."""

    phrase: str = Field(min_length=1)
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)
    literal: str
    confidence: float = Field(ge=0.0, le=1.0)


class PerceptorConfig(ContractModel):
    """Online perceptor budgets and thresholds from ``Interfaces.md``."""

    column_top_k: int = Field(default=5, ge=1)
    value_description_top_k: int = Field(default=3, ge=1)
    value_vector_top_k: int = Field(default=3, ge=1)
    value_search_workers: int = Field(default=4, ge=1)
    value_search_max_calls: int = Field(default=80, ge=1)
    column_score_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    value_score_threshold: float = Field(default=0.55, ge=0.0, le=1.0)


class PerceptionResult(ContractModel):
    """Output of fuzzy value linking before SQL generation."""

    catalog_version: str = Field(min_length=1)
    linked_values: tuple[LinkedValue, ...] = ()
    requires_clarification: bool = False
    clarification_reason: str | None = None

    @model_validator(mode="after")
    def _clarification_has_a_reason(self) -> PerceptionResult:
        if self.requires_clarification and not self.clarification_reason:
            raise ValueError("clarification requires a reason")
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


class IntendedColumn(ContractModel):
    """A column the generated SQL is expected to produce."""

    name: str = Field(min_length=1)
    role: ColumnRole


class ModelUsage(ContractModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)


class SqlCandidate(ContractModel):
    """Step 7 output, field-for-field from ``Interfaces.md``."""

    candidate_id: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    dialect: str = Field(min_length=1)
    origin: SqlOrigin
    intended_columns: tuple[IntendedColumn, ...] = ()
    grain: tuple[str, ...] = ()
    uses_edges: tuple[str, ...] = ()
    model: ModelUsage | None = None
    icl_exemplars_used: tuple[str, ...] | None = None
    repair_of: str | None = None

    @model_validator(mode="after")
    def _repairs_name_the_failed_candidate(self) -> SqlCandidate:
        if self.origin == "repair" and not self.repair_of:
            raise ValueError("a repair candidate must name repair_of")
        return self


class AllowlistedColumn(ContractModel):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)


class SchemaAllowlist(ContractModel):
    """The exact tables and columns a generated/repair candidate may use."""

    tables: tuple[str, ...] = ()
    columns: tuple[AllowlistedColumn, ...] = ()

    @property
    def table_set(self) -> frozenset[str]:
        return frozenset(table.lower() for table in self.tables)

    @property
    def column_set(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (column.table.lower(), column.column.lower())
            for column in self.columns
        )

    def permits_table(self, table: str) -> bool:
        return table.lower() in self.table_set

    def permits_column(self, table: str, column: str) -> bool:
        return (table.lower(), column.lower()) in self.column_set


class RepairError(ContractModel):
    kind: RepairErrorKind
    message: str = Field(min_length=1)


class RepairRequest(ContractModel):
    """Step 8b input. The allowlist is carried unchanged into every attempt."""

    attempt: Literal[1, 2]
    failed_sql: str = Field(min_length=1)
    failed_candidate_id: str | None = None
    error: RepairError
    allowlist: SchemaAllowlist
    reflector_score_threshold: float = Field(default=0.9, ge=0.0, le=1.0)


class ClarificationResult(ContractModel):
    """SQL-13 structured clarification envelope."""

    what_understood: str = Field(min_length=1)
    what_needs_clarification: str = Field(min_length=1)


class RepairAttempt(ContractModel):
    attempt: Literal[1, 2]
    request: RepairRequest
    candidate: SqlCandidate | None = None
    validation: ValidationResult | None = None
    execution: ExecutionResult | None = None

    @model_validator(mode="after")
    def _request_matches_attempt(self) -> RepairAttempt:
        if self.request.attempt != self.attempt:
            raise ValueError("repair attempt and request.attempt must match")
        return self


class RepairOutcome(ContractModel):
    success: bool
    candidate: SqlCandidate | None = None
    validation: ValidationResult | None = None
    execution: ExecutionResult | None = None
    attempts: tuple[RepairAttempt, ...] = Field(default=(), max_length=2)
    clarification: ClarificationResult | None = None

    @model_validator(mode="after")
    def _outcome_is_complete(self) -> RepairOutcome:
        if self.success:
            if (
                self.candidate is None
                or self.validation is None
                or self.execution is None
            ):
                raise ValueError(
                    "a successful repair outcome needs candidate, validation and execution"
                )
            if self.execution.status not in ("ok", "truncated"):
                raise ValueError(
                    "a successful repair outcome needs an ok or truncated execution"
                )
            if self.clarification is not None:
                raise ValueError("a successful outcome cannot carry a clarification")
        elif self.clarification is None:
            raise ValueError("a failed repair outcome needs a clarification")
        return self


__all__ = [
    "AllowlistedColumn",
    "ClarificationResult",
    "IntendedColumn",
    "ModelUsage",
    "RepairAttempt",
    "RepairError",
    "RepairErrorKind",
    "RepairOutcome",
    "RepairRequest",
    "SchemaAllowlist",
    "SqlCandidate",
    "SqlOrigin",
    "AnswerabilityResult",
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
    "LinkedValue",
    "PerceptionResult",
    "PerceptorConfig",
    "ValidationCheck",
    "ValidationCheckName",
    "ValidationCheckStatus",
    "ValidationMode",
    "ValidationResult",
]
