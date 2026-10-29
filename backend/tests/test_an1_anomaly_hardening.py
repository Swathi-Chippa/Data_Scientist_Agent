"""AN-1 Phase 2 hardening tests for the anomaly analyst."""

from __future__ import annotations

import math
from datetime import date

import pytest

from analysts.anomaly import AnomalyAnalyst
from contracts.models import (
    AnalystColumn,
    AnalystInput,
    AnalystOutput,
    AnomalyResult,
    Period,
    TableRef,
)


def _month_labels(
    count: int,
    *,
    start_year: int = 2017,
    start_month: int = 1,
) -> list[str]:
    labels = []
    year = start_year
    month = start_month
    for _ in range(count):
        labels.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return labels


def _table(rows, *, volume: bool = False) -> TableRef:
    columns = [
        AnalystColumn(name="month", type="TEXT", role="time"),
        AnalystColumn(name="order_count", type="REAL", role="measure"),
    ]
    if volume:
        columns.append(AnalystColumn(name="volume", type="REAL", role="measure"))
    return TableRef(
        query_id="Q-ANOM",
        columns=tuple(columns),
        row_count=len(rows),
        uri="inline://Q-ANOM",
        rows=tuple(rows),
    )


def _params(
    *,
    test_start: date = date(2018, 12, 1),
    test_end: date = date(2019, 1, 1),
    history_start: date = date(2017, 1, 1),
    history_end: date = date(2018, 12, 1),
) -> dict:
    return {
        "metric": "order_count",
        "grain": "month",
        "test_window": Period(start=test_start, end=test_end),
        "history_window": Period(start=history_start, end=history_end),
        "alpha": 0.05,
        "min_volume": 0,
    }


def _run_table(
    table: TableRef,
    *,
    params: dict | None = None,
    analyst: AnomalyAnalyst | None = None,
) -> AnalystOutput:
    analyst_input = AnalystInput(
        module="anomaly",
        params=params or _params(),
        tables={"series": table},
        run_id="RUN-AN1-ANOMALY",
    )
    return (analyst or AnomalyAnalyst()).run(analyst_input)


def _run_values(
    values,
    *,
    volumes=None,
    analyst: AnomalyAnalyst | None = None,
    params: dict | None = None,
) -> AnalystOutput:
    labels = _month_labels(len(values))
    rows = []
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        if volumes is None:
            rows.append((label, value))
        else:
            rows.append((label, value, volumes[index]))
    return _run_table(
        _table(rows, volume=volumes is not None),
        params=params,
        analyst=analyst,
    )


def test_anomaly_stable_series_remains_ok_and_finite():
    output = _run_values([100] * 23 + [102])

    assert output.status == "ok"
    assert isinstance(output.result, AnomalyResult)
    assert output.result.verdict == "within_normal_range"
    assert all(
        math.isfinite(value)
        for value in (
            output.result.actual,
            output.result.expected,
            output.result.deviation_pct,
            output.result.robust_score,
            output.result.threshold,
            *output.result.expected_range,
        )
    )


def test_anomaly_flags_planted_drop_with_explicit_safeguards():
    output = _run_values([100] * 23 + [20])

    assert output.status == "ok"
    assert isinstance(output.result, AnomalyResult)
    assert output.result.verdict == "unusual"
    assert output.result.actual == 20.0
    assert output.result.expected == pytest.approx(100.0, abs=1.0)
    assert output.result.deviation_pct == pytest.approx(-80.0, abs=1.0)
    assert output.result.persistence == "one_off"
    assert output.parameters["min_history_periods"] == 12
    assert output.parameters["min_observations"] == 30
    assert output.parameters["observation_count"] is None
    assert any("not PELT" in note for note in output.assumptions)
    assert any("No calendar" in note for note in output.assumptions)


def test_anomaly_empty_series_is_insufficient():
    output = _run_table(_table([]))

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "no usable" in output.reason.lower()


def test_anomaly_missing_measure_column_is_insufficient():
    table = TableRef(
        query_id="Q-NO-MEASURE",
        columns=(AnalystColumn(name="month", type="TEXT", role="time"),),
        row_count=1,
        uri="inline://Q-NO-MEASURE",
        rows=(("2018-01",),),
    )

    output = _run_table(table)

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "measure" in output.reason.lower()


def test_anomaly_fewer_than_twelve_history_periods_is_insufficient():
    labels = _month_labels(12)
    rows = [(label, 100) for label in labels[:-1]] + [(labels[-1], 20)]
    output = _run_table(
        _table(rows),
        params=_params(
            test_start=date(2017, 12, 1),
            test_end=date(2018, 1, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2017, 12, 1),
        ),
    )

    assert output.status == "insufficient_evidence"
    assert output.reason
    assert "history" in output.reason.lower()
    assert "12" in output.reason


def test_anomaly_exactly_twelve_history_periods_is_analyzed():
    labels = _month_labels(13)
    rows = [(label, 100) for label in labels[:-1]] + [(labels[-1], 20)]
    output = _run_table(
        _table(rows),
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 2, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )

    assert output.status == "ok"
    assert isinstance(output.result, AnomalyResult)
    assert output.result.verdict == "unusual"


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_anomaly_non_finite_measure_is_insufficient(bad_value):
    output = _run_values([100] * 23 + [bad_value])

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "finite" in output.reason.lower()


def test_anomaly_zero_baseline_is_insufficient():
    output = _run_values(
        [0] * 12 + [5],
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 2, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "zero" in output.reason.lower()


def test_anomaly_near_zero_baseline_is_insufficient():
    output = _run_values(
        [1e-12] * 12 + [5],
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 2, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "near zero" in output.reason.lower()


def test_anomaly_duplicate_and_unsorted_timestamps_are_deterministic():
    labels = _month_labels(13)
    duplicate_rows = [
        (labels[0], 50),
        (labels[0], 50),
        *[(label, 100) for label in labels[1:-1]],
        (labels[-1], 20),
    ]
    output = _run_table(
        _table(duplicate_rows),
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 2, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )
    reversed_output = _run_table(
        _table(list(reversed(duplicate_rows))),
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 2, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )

    assert output.status == "ok"
    assert output == reversed_output
    assert isinstance(output.result, AnomalyResult)
    assert output.result.actual == 20.0
    assert output.result.expected == pytest.approx(100.0)


def test_anomaly_sparse_history_with_short_recent_run_is_insufficient():
    labels = _month_labels(15)
    history_labels = [label for label in labels[:-2] if label != "2017-06"]
    rows = [(label, 100) for label in history_labels]
    rows.append((labels[-2], 20))
    output = _run_table(
        _table(rows),
        params=_params(
            test_start=date(2018, 2, 1),
            test_end=date(2018, 3, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 2, 1),
        ),
    )

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "history" in output.reason.lower()
    assert "consecutive" in output.reason.lower()


def test_anomaly_sparse_history_uses_latest_contiguous_run():
    labels = _month_labels(26, start_year=2016)
    history_labels = [label for label in labels[:-1] if label != "2016-06"]
    rows = [(label, 100) for label in history_labels]
    rows.append((labels[-1], 20))
    output = _run_table(
        _table(rows),
        params=_params(
            test_start=date(2018, 2, 1),
            test_end=date(2018, 3, 1),
            history_start=date(2016, 1, 1),
            history_end=date(2018, 2, 1),
        ),
    )

    assert output.status == "ok"
    assert isinstance(output.result, AnomalyResult)
    assert output.result.actual == 20.0
    assert output.result.expected == pytest.approx(100.0)
    assert any("sparse" in note.lower() for note in output.assumptions)


def test_anomaly_sparse_test_window_is_insufficient():
    labels = _month_labels(13)
    rows = [(label, 100) for label in labels[:-2]]
    rows.append((labels[-1], 20))
    output = _run_table(
        _table(rows),
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 3, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "missing" in output.reason.lower()


def test_anomaly_incomplete_boundary_period_is_dropped():
    output = _run_values(
        [100] * 12 + [20],
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 1, 15),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "complete" in output.reason.lower()


def test_anomaly_minimum_observation_guard_uses_volume():
    output = _run_values(
        [100] * 12 + [20],
        volumes=[1] * 13,
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 2, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )

    assert output.status == "insufficient_evidence"
    assert output.result is None
    assert output.reason
    assert "observations" in output.reason.lower()
    assert "30" in output.reason


def test_anomaly_exactly_thirty_observations_is_allowed():
    output = _run_values(
        [100] * 12 + [20],
        volumes=[2] * 12 + [6],
        params=_params(
            test_start=date(2018, 1, 1),
            test_end=date(2018, 2, 1),
            history_start=date(2017, 1, 1),
            history_end=date(2018, 1, 1),
        ),
    )

    assert output.status == "ok"
    assert isinstance(output.result, AnomalyResult)
    assert output.parameters["observation_count"] == 30.0


def test_anomaly_calendar_effects_are_not_claimed():
    output = _run_values(
        [100] * 12 + [20],
        params={
            **_params(
                test_start=date(2018, 1, 1),
                test_end=date(2018, 2, 1),
                history_start=date(2017, 1, 1),
                history_end=date(2018, 1, 1),
            ),
            "use_calendar": True,
        },
    )

    assert output.status == "ok"
    assert isinstance(output.result, AnomalyResult)
    assert output.result.calendar_effects == ()
    assert any("No calendar" in note for note in output.assumptions)
