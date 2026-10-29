"""Pure anomaly analysis over one prepared time series (ANOM-1..7)."""

from __future__ import annotations

import math
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
_TIME_NAMES = {"month", "period", "date", "week", "quarter", "year"}
_VOLUME_NAMES = {
    "count",
    "n",
    "n_obs",
    "num_observations",
    "observation_count",
    "observations",
    "row_count",
    "sample_size",
    "support",
    "volume",
}


class AnomalyAnalyst:
    """Analyze one prepared series with explicit, configurable safeguards."""

    module = "anomaly"

    def __init__(
        self,
        *,
        min_history_periods: int = 12,
        min_observations: int = 30,
        zero_baseline_tolerance: float = 1e-9,
    ) -> None:
        if min_history_periods < 1:
            raise ValueError("min_history_periods must be at least 1")
        if min_observations < 1:
            raise ValueError("min_observations must be at least 1")
        if (
            not math.isfinite(zero_baseline_tolerance)
            or zero_baseline_tolerance < 0.0
        ):
            raise ValueError(
                "zero_baseline_tolerance must be a finite non-negative number"
            )
        self.min_history_periods = min_history_periods
        self.min_observations = min_observations
        self.zero_baseline_tolerance = zero_baseline_tolerance

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

        columns = _series_columns(table, params.metric)
        if columns is None:
            return _insufficient(
                "The series table needs a time column and a numeric measure "
                "column.",
                analyst_input,
                table,
            )
        time_index, value_index, volume_index = columns
        series, volumes, problem = _aggregate_series(
            table,
            time_index,
            value_index,
            volume_index,
            params.grain,
        )
        if problem is not None:
            return _insufficient(problem, analyst_input, table)
        if not series:
            return _insufficient(
                "No usable time periods were found in the series table.",
                analyst_input,
                table,
            )

        test_rows = _complete_periods(series, params.test_window, params.grain)
        if not test_rows:
            return _insufficient(
                "No complete observations fall inside the requested test "
                "window.",
                analyst_input,
                table,
            )
        expected_test_periods = _periods_in_window(
            params.test_window,
            params.grain,
        )
        if [period for period, _ in test_rows] != expected_test_periods:
            return _insufficient(
                "The test window is missing complete periods; complete "
                "consecutive periods are required for an anomaly call.",
                analyst_input,
                table,
            )
        try:
            actual = math.fsum(value for _, value in test_rows)
        except OverflowError:
            return _insufficient(
                "The test-window values overflow while being aggregated.",
                analyst_input,
                table,
            )

        history_end = (
            params.history_window.end
            if params.history_window is not None
            else params.test_window.start
        )
        requested_history = [
            (period, value)
            for period, value in series
            if period < params.test_window.start
            and (
                params.history_window is None
                or params.history_window.start <= period
            )
            and _period_end(period, params.grain) <= history_end
        ]
        history = _latest_contiguous_run(requested_history, params.grain)
        if len(history) < self.min_history_periods:
            return _insufficient(
                f"Insufficient history: at least {self.min_history_periods} "
                "complete consecutive periods are required before the test "
                "window.",
                analyst_input,
                table,
            )
        if _period_end(history[-1][0], params.grain) != params.test_window.start:
            return _insufficient(
                "The history is not contiguous with the test window.",
                analyst_input,
                table,
            )
        sparse_history = len(history) != len(requested_history)

        observation_count = None
        if volume_index is not None:
            try:
                observation_count = math.fsum(
                    volumes.get(period, 0.0) for period, _ in history
                ) + math.fsum(
                    volumes.get(period, 0.0) for period, _ in test_rows
                )
            except OverflowError:
                return _insufficient(
                    "The observation volume overflows while being aggregated.",
                    analyst_input,
                    table,
                )
            if not math.isfinite(observation_count):
                return _insufficient(
                    "The observation volume is not finite.",
                    analyst_input,
                    table,
                )
            if observation_count < self.min_observations:
                return _insufficient(
                    "Insufficient observations: at least "
                    f"{self.min_observations} volume-weighted observations "
                    "are required for an anomaly call.",
                    analyst_input,
                    table,
                )

        baseline = _baseline(history, test_rows[0][0], params.grain)
        expected = baseline.expected
        scale = baseline.scale
        if not math.isfinite(expected) or not math.isfinite(scale) or scale <= 0.0:
            return _insufficient(
                "The baseline is not finite; a stable baseline is required.",
                analyst_input,
                table,
            )
        if abs(expected) <= self.zero_baseline_tolerance:
            return _insufficient(
                "The baseline is zero or near zero; percentage and robust-score "
                "changes would be misleading.",
                analyst_input,
                table,
            )

        threshold = max(
            2.5, NormalDist().inv_cdf(1.0 - params.alpha / 2.0)
        )
        low = expected - threshold * scale
        high = expected + threshold * scale
        score = (actual - expected) / scale
        deviation_pct = (actual - expected) / expected * 100.0
        if not all(
            math.isfinite(item)
            for item in (actual, expected, scale, threshold, low, high, score, deviation_pct)
        ):
            return _insufficient(
                "The anomaly calculation produced a non-finite value.",
                analyst_input,
                table,
            )
        verdict = "unusual" if actual < low or actual > high else "within_normal_range"
        analysis_series = sorted(history + test_rows)
        persistence = _persistence(
            analysis_series,
            test_window=params.test_window,
            low=low,
            high=high,
            scale=scale,
            verdict=verdict,
        )
        assumptions = [
            "The test window is excluded from the baseline.",
            baseline.note,
            "The change check is a two-segment sum-of-squares scan, not PELT.",
            "No calendar adjustment was applied; use_calendar is retained for "
            "contract compatibility.",
            "No multiple-testing correction was applied because only one "
            "series was scanned.",
        ]
        if sparse_history:
            assumptions.append(
                "Earlier sparse history was excluded; the baseline uses the "
                "most recent contiguous run."
            )
        if volume_index is None:
            assumptions.append(
                "No volume column was supplied, so the observation-count guard "
                "was not applied."
            )
        if params.series_dimension is not None:
            assumptions.append(
                f"The analysis covers the series dimension "
                f"{params.series_dimension!r} only."
            )

        result = AnomalyResult(
            verdict=verdict,
            window=_window_label(params),
            actual=float(actual),
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
            parameters=_parameter_dict(params, self, observation_count),
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


def _series_columns(
    table: TableRef,
    metric: str,
) -> tuple[int, int, int | None] | None:
    time_index = None
    measures: list[tuple[int, str, str]] = []
    for index, column in enumerate(table.columns):
        name = column.name.lower()
        role = column.role
        if time_index is None and (
            role == "time"
            or name in _TIME_NAMES
            or name.endswith("_date")
            or name.endswith("_month")
        ):
            time_index = index
            continue
        if (
            role == "measure"
            or name in {"value", "measure", "order_count", "revenue", "orders"}
        ):
            measures.append((index, name, role))

    if time_index is None or not measures:
        return None

    metric_name = metric.lower()
    value_index = next(
        (index for index, name, _ in measures if name == metric_name),
        None,
    )
    if value_index is None:
        non_volume = [
            index
            for index, name, _ in measures
            if name not in _VOLUME_NAMES
        ]
        value_index = non_volume[0] if non_volume else measures[0][0]

    volume_index = next(
        (
            index
            for index, name, _ in measures
            if index != value_index and name in _VOLUME_NAMES
        ),
        None,
    )
    return time_index, value_index, volume_index


def _aggregate_series(
    table: TableRef,
    time_index: int,
    value_index: int,
    volume_index: int | None,
    grain: str,
) -> tuple[
    list[tuple[date, float]],
    dict[date, float],
    str | None,
]:
    grouped: dict[date, list[float]] = {}
    volumes: dict[date, list[float]] = {}
    for row in table.rows:
        if max(time_index, value_index) >= len(row):
            continue
        if volume_index is not None and volume_index >= len(row):
            return [], {}, "The series row is missing its volume value."
        period = _period_start(row[time_index], grain)
        if period is None:
            continue
        try:
            value = float(row[value_index])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            return (
                [],
                {},
                "The series contains a non-finite measure value; finite values "
                "are required.",
            )
        grouped.setdefault(period, []).append(value)

        if volume_index is not None:
            try:
                volume = float(row[volume_index])
            except (TypeError, ValueError):
                return [], {}, "The series contains a non-numeric volume value."
            if not math.isfinite(volume):
                return (
                    [],
                    {},
                    "The series contains a non-finite volume value; finite "
                    "values are required.",
                )
            if volume < 0.0:
                return (
                    [],
                    {},
                    "The series contains a negative volume value; observation "
                    "counts must be non-negative.",
                )
            volumes.setdefault(period, []).append(volume)

    series: list[tuple[date, float]] = []
    for period in sorted(grouped):
        try:
            total = math.fsum(sorted(grouped[period]))
        except OverflowError:
            return [], {}, "The series values overflow while being aggregated."
        series.append((period, total))

    volume_totals: dict[date, float] = {}
    for period in sorted(volumes):
        try:
            volume_totals[period] = math.fsum(sorted(volumes[period]))
        except OverflowError:
            return [], {}, "The volumes overflow while being aggregated."
    return series, volume_totals, None


def _complete_periods(
    series: list[tuple[date, float]],
    window,
    grain: str,
) -> list[tuple[date, float]]:
    return [
        (period, value)
        for period, value in series
        if window.start <= period < window.end
        and _period_end(period, grain) <= window.end
    ]


def _periods_in_window(window, grain: str) -> list[date]:
    current = _truncate(window.start, grain)
    if current < window.start:
        current = _period_end(current, grain)
    periods: list[date] = []
    while current < window.end:
        if _period_end(current, grain) <= window.end:
            periods.append(current)
        current = _period_end(current, grain)
    return periods


def _latest_contiguous_run(
    series: list[tuple[date, float]],
    grain: str,
) -> list[tuple[date, float]]:
    if not series:
        return []
    start = len(series) - 1
    while start > 0 and (
        _period_end(series[start - 1][0], grain) == series[start][0]
    ):
        start -= 1
    return series[start:]


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


def _period_end(start: date, grain: str) -> date:
    if grain == "year":
        return date(start.year + 1, 1, 1)
    if grain == "quarter":
        month = start.month + 3
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return date(year, month, 1)
    if grain == "month":
        month = start.month + 1
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return date(year, month, 1)
    if grain == "week":
        return start + timedelta(days=7)
    return start + timedelta(days=1)


def _window_label(params: AnomalyParams) -> str:
    return f"{params.test_window.start.isoformat()}/{params.test_window.end.isoformat()}"


def _parameter_dict(
    params: AnomalyParams,
    analyst: AnomalyAnalyst,
    observation_count: float | None,
) -> dict[str, object]:
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
        "min_history_periods": analyst.min_history_periods,
        "min_observations": analyst.min_observations,
        "zero_baseline_tolerance": analyst.zero_baseline_tolerance,
        "observation_count": observation_count,
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
