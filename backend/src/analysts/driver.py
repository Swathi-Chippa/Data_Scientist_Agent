"""Pure variance attribution over a prepared contribution table (DRV)."""

from __future__ import annotations

import math
import re
from datetime import date
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
    module = "driver"

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
        columns = _driver_columns(table, params.dimensions)
        if columns is None:
            return _insufficient(
                "The contribution table needs a period column, a numeric "
                "value column and every requested dimension.",
                analyst_input,
                table,
            )
        period_index, value_index, dimensions = columns
        current: dict[tuple[str, ...], float] = {}
        comparison: dict[tuple[str, ...], float] = {}
        samples: dict[tuple[str, ...], int] = {}
        for row in table.rows:
            if max(period_index, value_index, *dimensions.values()) >= len(row):
                continue
            period = _as_date(row[period_index])
            if period is None:
                continue
            try:
                value = float(row[value_index])
            except (TypeError, ValueError):
                continue
            key = tuple(
                str(row[index]).strip() for index in dimensions.values()
            )
            if params.current_period.start <= period < params.current_period.end:
                current[key] = current.get(key, 0.0) + value
                samples[key] = samples.get(key, 0) + 1
            elif (
                params.comparison_period.start
                <= period
                < params.comparison_period.end
            ):
                comparison[key] = comparison.get(key, 0.0) + value

        if not current or not comparison:
            return _insufficient(
                "Both the current and comparison windows need observations.",
                analyst_input,
                table,
            )

        current_total = sum(current.values())
        comparison_total = sum(comparison.values())
        total_change = current_total - comparison_total
        keys = sorted(
            set(current) | set(comparison),
            key=lambda key: (-abs(current.get(key, 0.0) - comparison.get(key, 0.0)), key),
        )
        contributions = [
            current.get(key, 0.0) - comparison.get(key, 0.0) for key in keys
        ]
        drivers = tuple(
            _contribution(
                key=key,
                contribution=contribution,
                total_change=total_change,
                sample_size=samples.get(key, 0),
                dimensions=params.dimensions,
            )
            for key, contribution in zip(keys, contributions, strict=True)
        )
        reconciliation_error = total_change - sum(contributions)
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
            },
            assumptions=(
                "Contributions are exact current-minus-comparison changes for "
                "the prepared dimension rows.",
                "The interval is a normal approximation over the aggregate "
                "row count, not a row-level bootstrap.",
                _CAUSAL_NOTE,
            ),
            sources=(table.query_id,),
        )


def _contribution(
    *,
    key: tuple[str, ...],
    contribution: float,
    total_change: float,
    sample_size: int,
    dimensions: tuple[str, ...],
) -> DriverContribution:
    share = (
        contribution / total_change
        if abs(total_change) > 1e-12
        else 0.0
    )
    half_width = (
        1.96 * abs(contribution) / math.sqrt(sample_size)
        if sample_size > 0
        else abs(contribution)
    )
    unstable = sample_size < 5 or abs(total_change) <= 1e-12
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
