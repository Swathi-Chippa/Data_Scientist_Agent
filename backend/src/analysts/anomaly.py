"""Pure anomaly analysis over one prepared time series (ANOM-1..7)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from statistics import NormalDist

import numpy as np

from contracts.models import (
    AnalystInput,
    AnalystOutput,
    AnomalyParams,
    AnomalyResult,
    TableRef,
)

_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")
_QUARTER_PATTERN = re.compile(r"^(\d{4})-?Q([1-4])$", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"^(\d{4})$")


class AnomalyAnalyst:
    module = "anomaly"

    def run(self, analyst_input: AnalystInput) -> AnalystOutput:
        table = analyst_input.tables.get("series")
        if table is None:
            return _insufficient(
                "The anomaly analyst requires a 'series' table.",
                analyst_input,
            )
        try:
            params = AnomalyParams.model_validate(analyst_input.params)
        except Exception as exc:
            return _insufficient(
                f"Invalid anomaly parameters: {exc}",
                analyst_input,
                table,
            )

        columns = _series_columns(table)
        if columns is None:
            return _insufficient(
                "The series table needs a time column and a numeric measure "
                "column.",
                analyst_input,
                table,
            )
        time_index, value_index = columns
        series = _aggregate_series(table, time_index, value_index, params.grain)
        if not series:
            return _insufficient(
                "No usable time periods were found in the series table.",
                analyst_input,
                table,
            )

        test_rows = [
            (period, value)
            for period, value in series
            if params.test_window.start <= period < params.test_window.end
        ]
        if not test_rows:
            return _insufficient(
                "No observations fall inside the requested test window.",
                analyst_input,
                table,
            )
        actual = float(sum(value for _, value in test_rows))
        if actual < params.min_volume:
            return _insufficient(
                "The result volume is below the minimum-volume threshold; "
                "there is not enough data to call an anomaly.",
                analyst_input,
                table,
            )

        history = [
            (period, value)
            for period, value in series
            if period < params.test_window.start
        ]
        if params.history_window is not None:
            history = [
                (period, value)
                for period, value in history
                if params.history_window.start <= period
                < params.history_window.end
            ]
        if len(history) < 3:
            return _insufficient(
                "Insufficient history: at least three complete periods are "
                "required before the test window.",
                analyst_input,
                table,
            )

        baseline = _baseline(history, test_rows[0][0], params.grain)
        expected = baseline.expected
        scale = baseline.scale
        threshold = max(
            2.5, NormalDist().inv_cdf(1.0 - params.alpha / 2.0)
        )
        low = expected - threshold * scale
        high = expected + threshold * scale
        score = (actual - expected) / scale
        deviation_pct = (
            (actual - expected) / expected * 100.0 if expected != 0 else 0.0
        )
        verdict = "unusual" if actual < low or actual > high else "within_normal_range"
        persistence = _persistence(
            series,
            test_window=params.test_window,
            low=low,
            high=high,
            scale=scale,
            verdict=verdict,
        )
        assumptions = [
            "The test window is excluded from the baseline.",
            baseline.note,
            "A PELT-style two-segment change check distinguishes one-off "
            "spikes from sustained shifts; no other series were scanned.",
        ]
        if params.series_dimension is not None:
            assumptions.append(
                f"The analysis covers the series dimension "
                f"{params.series_dimension!r} only."
            )

        result = AnomalyResult(
            verdict=verdict,
            window=_window_label(params),
            actual=actual,
            expected=float(expected),
            expected_range=(float(low), float(high)),
            deviation_pct=float(deviation_pct),
            robust_score=float(score),
            persistence=persistence,
            threshold=float(threshold),
            correction_used=None,
            calendar_effects=(),
            baseline_kind=baseline.kind,
        )
        return AnalystOutput(
            module="anomaly",
            status="ok",
            result=result,
            parameters=_parameter_dict(params),
            assumptions=tuple(assumptions),
            sources=(table.query_id,),
        )


class _Baseline:
    def __init__(
        self,
        expected: float,
        scale: float,
        kind: str,
        note: str,
    ) -> None:
        self.expected = expected
        self.scale = scale
        self.kind = kind
        self.note = note


def _baseline(
    history: list[tuple[date, float]],
    target_period: date,
    grain: str,
) -> _Baseline:
    values = np.asarray([value for _, value in history], dtype=float)
    if grain == "month" and len(history) >= 24:
        periods = [period for period, _ in history]
        global_center = float(np.median(values))
        factors: dict[int, float] = {}
        if global_center != 0:
            for month in range(1, 13):
                month_values = [
                    value
                    for period, value in history
                    if period.month == month
                ]
                if month_values:
                    factors[month] = float(
                        np.median(np.asarray(month_values, dtype=float))
                        / global_center
                    )
        factors = {
            month: min(max(factor, 0.25), 4.0)
            for month, factor in factors.items()
        }
        adjusted = np.asarray(
            [
                value / factors.get(period.month, 1.0)
                for period, value in history
            ],
            dtype=float,
        )
        center = float(np.median(adjusted))
        residuals = adjusted - center
        scale = _robust_scale(residuals, center)
        expected = center * factors.get(target_period.month, 1.0)
        return _Baseline(
            expected=expected,
            scale=scale,
            kind="seasonal",
            note=(
                "A month-of-year seasonal median baseline was used because "
                "the history spans at least two years."
            ),
        )

    trailing = values[-12:]
    center = float(np.median(trailing))
    scale = _robust_scale(trailing - center, center)
    return _Baseline(
        expected=center,
        scale=scale,
        kind="robust_trailing",
        note=(
            "A robust trailing median/MAD baseline was used because the "
            "history is shorter than the seasonal minimum."
        ),
    )


def _robust_scale(residuals: np.ndarray, center: float) -> float:
    mad = float(np.median(np.abs(residuals)))
    scale = 1.4826 * mad
    if scale <= 0.0 and residuals.size > 1:
        scale = float(np.std(residuals, ddof=1))
    if scale <= 0.0:
        scale = max(abs(center) * 0.05, 1.0)
    return scale


def _persistence(
    series: list[tuple[date, float]],
    *,
    test_window,
    low: float,
    high: float,
    scale: float,
    verdict: str,
) -> str:
    if verdict != "unusual":
        return "unknown"
    recent = series[-12:]
    values = np.asarray([value for _, value in recent], dtype=float)
    split = _change_point(values)
    if split is not None:
        if split <= len(values) - 2:
            return "sustained"
        return "one_off"
    if len(series) >= 3:
        prior_period, prior_value = series[-2]
        current_period, current_value = series[-1]
        if prior_period >= test_window.start or current_period >= test_window.start:
            if prior_value < low and current_value < low:
                return "sustained"
            if prior_value > high and current_value > high:
                return "sustained"
    if len(values) >= 6:
        x = np.arange(len(values), dtype=float)
        slope = float(np.polyfit(x, values, 1)[0])
        if abs(slope) > scale / 6.0:
            return "drift"
    return "one_off"


def _change_point(values: np.ndarray) -> int | None:
    count = len(values)
    if count < 6:
        return None
    total = float(np.sum((values - np.mean(values)) ** 2))
    if total <= 0.0:
        return None
    best_split: int | None = None
    best_error = total
    for split in range(2, count):
        left = values[:split]
        right = values[split:]
        error = float(
            np.sum((left - np.mean(left)) ** 2)
            + np.sum((right - np.mean(right)) ** 2)
        )
        if error < best_error:
            best_error = error
            best_split = split
    if best_split is None:
        return None
    if best_error <= total * 0.65:
        return best_split
    return None


def _series_columns(table: TableRef) -> tuple[int, int] | None:
    time_index = None
    value_index = None
    for index, column in enumerate(table.columns):
        name = column.name.lower()
        role = column.role
        if time_index is None and (
            role == "time"
            or name in {"month", "period", "date", "week", "quarter", "year"}
            or name.endswith("_date")
            or name.endswith("_month")
        ):
            time_index = index
        if value_index is None and (
            role == "measure"
            or name in {"value", "measure", "order_count", "revenue", "orders"}
        ):
            value_index = index
    if time_index is None or value_index is None:
        return None
    return time_index, value_index


def _aggregate_series(
    table: TableRef,
    time_index: int,
    value_index: int,
    grain: str,
) -> list[tuple[date, float]]:
    grouped: dict[date, float] = {}
    for row in table.rows:
        if time_index >= len(row) or value_index >= len(row):
            continue
        period = _period_start(row[time_index], grain)
        if period is None:
            continue
        try:
            value = float(row[value_index])
        except (TypeError, ValueError):
            continue
        grouped[period] = grouped.get(period, 0.0) + value
    return sorted(grouped.items())


def _period_start(value: object, grain: str) -> date | None:
    parsed: date | None = None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        quarter = _QUARTER_PATTERN.match(text)
        if quarter is not None:
            year = int(quarter.group(1))
            parsed = date(year, (int(quarter.group(2)) - 1) * 3 + 1, 1)
        else:
            narrowed = _DATE_PATTERN.match(text)
            if narrowed is not None:
                year = int(narrowed.group(1))
                month = int(narrowed.group(2))
                day = int(narrowed.group(3) or 1)
                parsed = date(year, month, day)
            else:
                year_only = _YEAR_PATTERN.match(text)
                if year_only is not None:
                    parsed = date(int(year_only.group(1)), 1, 1)
    if parsed is None:
        return None
    return _truncate(parsed, grain)


def _truncate(value: date, grain: str) -> date:
    if grain == "year":
        return date(value.year, 1, 1)
    if grain == "quarter":
        return date(value.year, (value.month - 1) // 3 * 3 + 1, 1)
    if grain == "month":
        return date(value.year, value.month, 1)
    if grain == "week":
        return value - timedelta(days=value.weekday())
    return value


def _window_label(params: AnomalyParams) -> str:
    return f"{params.test_window.start.isoformat()}/{params.test_window.end.isoformat()}"


def _parameter_dict(params: AnomalyParams) -> dict[str, object]:
    return {
        "metric": params.metric,
        "grain": params.grain,
        "test_window": _window_label(params),
        "history_window": (
            f"{params.history_window.start.isoformat()}/"
            f"{params.history_window.end.isoformat()}"
            if params.history_window is not None
            else None
        ),
        "series_dimension": params.series_dimension,
        "alpha": params.alpha,
        "min_volume": params.min_volume,
        "use_calendar": params.use_calendar,
    }


def _insufficient(
    reason: str,
    analyst_input: AnalystInput,
    table: TableRef | None = None,
) -> AnalystOutput:
    return AnalystOutput(
        module="anomaly",
        status="insufficient_evidence",
        reason=reason,
        sources=(table.query_id,) if table is not None else (),
        parameters=dict(analyst_input.params),
    )


__all__ = ["AnomalyAnalyst"]
