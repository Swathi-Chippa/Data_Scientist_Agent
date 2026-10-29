"""Pure variance attribution over a prepared contribution table (DRV)."""

from __future__ import annotations

import math
import re
from datetime import date
from statistics import NormalDist
from typing import Any, Literal

from pydantic import Field

from contracts.models import (
    AnalystInput,
    AnalystOutput,
    ContractModel,
    DriverContribution,
    DriverResult,
    Period,
    TableRef,
)

_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")

_CAUSAL_NOTE = (
    "This analysis identifies the dimensions whose values moved with the "
    "metric. It is not causal evidence that those dimensions caused the "
    "change."
)
_E5_NOTE = (
    "Observed covariation indicates statistical association, not proven "
    "causality. Unmeasured exogenous variables and reverse causation can "
    "produce the same pattern."
)


class DriverParams(ContractModel):
    metric: str = Field(min_length=1)
    current_period: Period
    comparison_period: Period
    dimensions: tuple[str, ...] = Field(min_length=1)
    max_dimensions: int = Field(default=5, ge=1)
    max_depth: int = Field(default=2, ge=1)
    metric_tree: tuple[dict[str, Any], ...] = ()
    value_decomposition: bool = False
    indicators: tuple[dict[str, str], ...] = ()
    comparison_group: dict[str, Any] | None = None
    n_bootstrap: int = Field(default=200, ge=0)
    alpha: float = Field(default=0.05, gt=0.0, lt=0.5)
    aggregation: Literal["sum", "count", "avg"] = "sum"


class DriverAnalyst:
    """Attribute an additive change with explicit evidence guardrails."""

    module = "driver"

    def __init__(
        self,
        *,
        zero_change_tolerance: float = 1e-12,
        small_cell_threshold: int = 5,
    ) -> None:
        if (
            not math.isfinite(zero_change_tolerance)
            or zero_change_tolerance < 0.0
        ):
            raise ValueError(
                "zero_change_tolerance must be a finite non-negative number"
            )
        if small_cell_threshold < 1:
            raise ValueError("small_cell_threshold must be at least 1")
        self.zero_change_tolerance = zero_change_tolerance
        self.small_cell_threshold = small_cell_threshold

    def run(self, analyst_input: AnalystInput) -> AnalystOutput:
        table = analyst_input.tables.get("contrib")
        if table is None:
            return _insufficient(
                "The driver analyst requires a 'contrib' table.",
                analyst_input,
            )
        try:
            params = DriverParams.model_validate(analyst_input.params)
        except Exception as exc:
            return _insufficient(
                f"Invalid driver parameters: {exc}",
                analyst_input,
                table,
            )
        unsupported = _unsupported_reason(params)
        if unsupported is not None:
            return _insufficient(unsupported, analyst_input, table)

        columns = _driver_columns(table, params.dimensions)
        if columns is None:
            return _insufficient(
                "The contribution table needs a period column, a numeric "
                "value column and every requested dimension.",
                analyst_input,
                table,
            )
        period_index, value_index, dimensions = columns
        current_groups: dict[tuple[str, ...], list[float]] = {}
        comparison_groups: dict[tuple[str, ...], list[float]] = {}
        samples: dict[tuple[str, ...], int] = {}
        indexes = (period_index, value_index, *dimensions.values())
        for row in table.rows:
            if max(indexes) >= len(row):
                return _insufficient(
                    "A contribution row is missing one of its required values.",
                    analyst_input,
                    table,
                )
            period = _as_date(row[period_index])
            if period is None:
                return _insufficient(
                    "The contribution table contains an invalid period value.",
                    analyst_input,
                    table,
                )
            in_current = (
                params.current_period.start
                <= period
                < params.current_period.end
            )
            in_comparison = (
                params.comparison_period.start
                <= period
                < params.comparison_period.end
            )
            if not in_current and not in_comparison:
                continue
            try:
                value = float(row[value_index])
            except (TypeError, ValueError):
                return _insufficient(
                    "The contribution table contains a non-numeric value.",
                    analyst_input,
                    table,
                )
            if not math.isfinite(value):
                return _insufficient(
                    "The contribution table contains a non-finite value; "
                    "finite values are required.",
                    analyst_input,
                    table,
                )
            key_parts: list[str] = []
            for index in dimensions.values():
                raw = row[index]
                if raw is None:
                    return _insufficient(
                        "A requested dimension has a missing value.",
                        analyst_input,
                        table,
                    )
                text = str(raw).strip()
                if not text:
                    return _insufficient(
                        "A requested dimension has a blank value.",
                        analyst_input,
                        table,
                    )
                key_parts.append(text)
            key = tuple(key_parts)
            if in_current:
                current_groups.setdefault(key, []).append(value)
                samples[key] = samples.get(key, 0) + 1
            else:
                comparison_groups.setdefault(key, []).append(value)

        current, problem = _sum_groups(current_groups)
        if problem is not None:
            return _insufficient(problem, analyst_input, table)
        comparison, problem = _sum_groups(comparison_groups)
        if problem is not None:
            return _insufficient(problem, analyst_input, table)

        if not current and not comparison:
            return _insufficient(
                "Both the current and comparison windows need observations.",
                analyst_input,
                table,
            )
        if not current:
            return _insufficient(
                "The current window has no observations.",
                analyst_input,
                table,
            )
        if not comparison:
            return _insufficient(
                "The comparison window has no observations.",
                analyst_input,
                table,
            )

        try:
            current_total = math.fsum(sorted(current.values()))
            comparison_total = math.fsum(sorted(comparison.values()))
        except OverflowError:
            return _insufficient(
                "The contribution values overflow while being aggregated.",
                analyst_input,
                table,
            )
        total_change = current_total - comparison_total
        if not math.isfinite(total_change):
            return _insufficient(
                "The total change is not finite.",
                analyst_input,
                table,
            )
        if abs(total_change) <= self.zero_change_tolerance:
            return _insufficient(
                "Total change is zero or near zero; contribution shares are "
                "undefined.",
                analyst_input,
                table,
            )

        keys = sorted(
            set(current) | set(comparison),
            key=lambda key: (
                -abs(current.get(key, 0.0) - comparison.get(key, 0.0)),
                key,
            ),
        )
        contributions = [
            current.get(key, 0.0) - comparison.get(key, 0.0)
            for key in keys
        ]
        z_score = NormalDist().inv_cdf(1.0 - params.alpha / 2.0)
        drivers = tuple(
            _contribution(
                key=key,
                contribution=contribution,
                total_change=total_change,
                sample_size=samples.get(key, 0),
                dimensions=params.dimensions,
                z_score=z_score,
                small_cell_threshold=self.small_cell_threshold,
            )
            for key, contribution in zip(keys, contributions, strict=True)
        )
        try:
            reconciliation_error = total_change - math.fsum(contributions)
        except OverflowError:
            return _insufficient(
                "The contribution values overflow during reconciliation.",
                analyst_input,
                table,
            )
        if not _drivers_are_finite(
            total_change,
            reconciliation_error,
            drivers,
        ):
            return _insufficient(
                "The driver calculation produced a non-finite value.",
                analyst_input,
                table,
            )

        return AnalystOutput(
            module="driver",
            status="ok",
            result=DriverResult(
                metric=params.metric,
                current_period=_period_label(params.current_period),
                comparison_period=_period_label(params.comparison_period),
                total_change=float(total_change),
                drivers=drivers,
                reconciliation_error=float(reconciliation_error),
                candidates_examined=len(keys),
                correction_used=None,
            ),
            parameters={
                "metric": params.metric,
                "current_period": _period_label(params.current_period),
                "comparison_period": _period_label(params.comparison_period),
                "dimensions": params.dimensions,
                "aggregation": params.aggregation,
                "value_decomposition": params.value_decomposition,
                "max_dimensions": params.max_dimensions,
                "max_depth": params.max_depth,
                "n_bootstrap": params.n_bootstrap,
                "alpha": params.alpha,
                "zero_change_tolerance": self.zero_change_tolerance,
                "small_cell_threshold": self.small_cell_threshold,
            },
            assumptions=(
                "Contributions are exact current-minus-comparison changes for "
                "the prepared dimension rows.",
                "The interval is a normal approximation over the aggregate "
                "row count, not a row-level bootstrap.",
                "Drivers are single-level; no metric-tree traversal or "
                "multiple-testing correction was applied.",
                _CAUSAL_NOTE,
                _E5_NOTE,
            ),
            sources=(table.query_id,),
        )


def _unsupported_reason(params: DriverParams) -> str | None:
    if params.aggregation != "sum":
        return (
            "Only additive 'sum' decomposition is supported in this phase; "
            f"aggregation={params.aggregation!r} is not implemented."
        )
    if params.value_decomposition:
        return (
            "Price/volume/mix decomposition is not implemented in this phase."
        )
    if params.metric_tree:
        return "Metric-tree traversal is not implemented in this phase."
    if params.indicators:
        return (
            "Operational-indicator co-movement is not implemented in this "
            "phase."
        )
    if params.comparison_group is not None:
        return (
            "Difference-in-differences comparison groups are not implemented "
            "in this phase."
        )
    if len(params.dimensions) > params.max_dimensions:
        return (
            f"{len(params.dimensions)} dimensions were requested, exceeding "
            f"max_dimensions={params.max_dimensions}."
        )
    if len({dimension.lower() for dimension in params.dimensions}) != len(
        params.dimensions
    ):
        return "Requested dimensions must be unique."
    return None


def _sum_groups(
    groups: dict[tuple[str, ...], list[float]],
) -> tuple[dict[tuple[str, ...], float], str | None]:
    totals: dict[tuple[str, ...], float] = {}
    for key in sorted(groups):
        try:
            totals[key] = math.fsum(sorted(groups[key]))
        except OverflowError:
            return {}, "The contribution values overflow while being aggregated."
    return totals, None


def _contribution(
    *,
    key: tuple[str, ...],
    contribution: float,
    total_change: float,
    sample_size: int,
    dimensions: tuple[str, ...],
    z_score: float,
    small_cell_threshold: int,
) -> DriverContribution:
    share = contribution / total_change
    half_width = (
        z_score * abs(contribution) / math.sqrt(sample_size)
        if sample_size > 0
        else abs(contribution)
    )
    unstable = sample_size < small_cell_threshold
    return DriverContribution(
        dimension=" / ".join(dimensions),
        value=" / ".join(key),
        contribution=float(contribution),
        share=float(share),
        interval=(float(contribution - half_width), float(contribution + half_width)),
        sample_size=sample_size,
        unstable=unstable,
        grade=1 if unstable else 2,
        data_needed_to_strengthen=(
            "A longer comparison window with row-level observations.",
            "A bootstrap or hierarchical attribution model for interval "
            "estimation.",
        )
        if unstable
        else (),
    )


def _drivers_are_finite(
    total_change: float,
    reconciliation_error: float,
    drivers: tuple[DriverContribution, ...],
) -> bool:
    if not math.isfinite(total_change) or not math.isfinite(
        reconciliation_error
    ):
        return False
    for driver in drivers:
        values = (
            driver.contribution,
            driver.share,
            driver.interval[0],
            driver.interval[1],
        )
        if not all(math.isfinite(value) for value in values):
            return False
    return True


def _driver_columns(
    table: TableRef,
    requested: tuple[str, ...],
) -> tuple[int, int, dict[str, int]] | None:
    period_index = None
    value_index = None
    by_name: dict[str, int] = {}
    dimension_candidates: dict[str, int] = {}
    for index, column in enumerate(table.columns):
        name = column.name.lower()
        if period_index is None and (
            column.role == "time"
            or name in {"period", "month", "date", "week", "quarter", "year"}
            or name.endswith("_date")
        ):
            period_index = index
            continue
        if value_index is None and (
            column.role == "measure"
            or name in {"value", "measure", "metric_value", "contribution"}
        ):
            value_index = index
            continue
        by_name[name] = index
        if column.role == "dimension":
            dimension_candidates[name] = index

    resolved: dict[str, int] = {}
    remaining = list(dimension_candidates.items())
    for dimension in requested:
        lowered = dimension.lower()
        if lowered in by_name:
            resolved[dimension] = by_name[lowered]
            continue
        match = next(
            (
                index
                for name, index in remaining
                if lowered in name or name in lowered
            ),
            None,
        )
        if match is None:
            return None
        resolved[dimension] = match
    if period_index is None or value_index is None:
        return None
    if len(set(resolved.values())) != len(resolved):
        return None
    return period_index, value_index, resolved


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    match = _DATE_PATTERN.match(value.strip())
    if match is None:
        return None
    return date(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 1),
    )


def _period_label(period: Period) -> str:
    return f"{period.start.isoformat()}/{period.end.isoformat()}"


def _insufficient(
    reason: str,
    analyst_input: AnalystInput,
    table: TableRef | None = None,
) -> AnalystOutput:
    return AnalystOutput(
        module="driver",
        status="insufficient_evidence",
        reason=reason,
        sources=(table.query_id,) if table is not None else (),
        parameters=dict(analyst_input.params),
    )


__all__ = ["DriverAnalyst", "DriverParams"]
