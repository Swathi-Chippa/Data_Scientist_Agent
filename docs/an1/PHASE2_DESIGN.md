# Phase 2: Anomaly and Driver Analyst Hardening

**Status:** Implemented in this phase. This document covers only the anomaly and driver hardening. It does not claim that deferred capabilities are available.

## Ownership Boundary

Phase 2 changes only:

- `backend/src/analysts/anomaly.py`
- `backend/src/analysts/driver.py`
- `backend/tests/test_an1_anomaly_hardening.py`
- `backend/tests/test_an1_driver_hardening.py`
- this design note

The shared contracts, analyst harness, registry, orchestrator, service, SQL, gateway, evidence, router, frontend, and dependency list are unchanged. Analysts remain pure functions with typed input and output. They do not use a database, network, model client, subprocess, `eval`, `exec`, or unrestricted dynamic imports.

## Approved Decisions

The implementation follows the owner decisions for this phase:

1. Fewer than 12 complete history periods is insufficient evidence.
2. Observation sufficiency uses a configurable minimum of 30. It is not inferred from the metric value itself.
3. Bootstrap inference is deferred; no new dependencies are added.
4. The canonical runtime label remains `driver`, not `root_cause`.
5. Existing tests are unchanged; new hardening tests use the `test_an1_*.py` pattern.
6. Calendar effects and FDR correction are deferred and are not claimed.

## Anomaly Behavior

`AnomalyAnalyst` now applies explicit, configurable safeguards before returning a result.

### Period Coverage

- The test window and history window are filtered to complete periods.
- A period is complete only when its period end is at or before the requested window end.
- Incomplete boundary periods are excluded rather than treated as full periods.
- The history must contain at least 12 complete, consecutive periods by default.
- The test window must contain every expected complete period; a missing test period is insufficient.
- If the requested history contains a gap, the analyst uses the most recent contiguous run and states that earlier sparse history was excluded.
- If the most recent contiguous history run has fewer than 12 periods, the result is insufficient.
- The retained history must be contiguous with the test window; a gap before the test window is insufficient.

### Observation Sufficiency

- The constructor default is `min_observations=30`.
- When the prepared series includes an optional volume/observation column, the guard sums that volume over the analyzed history and test periods.
- If the volume is below the threshold, the result is `insufficient_evidence` with an explicit reason.
- If no volume column exists, the guard is not inferred from the metric value. The limitation is stated in the output assumptions.
- `min_volume` remains in the shared parameter contract for compatibility but is not used as evidence sufficiency. It must not be repurposed to infer sufficiency from the metric value.

### Baseline and Output Safety

- A zero or near-zero baseline, by default `abs(expected) <= 1e-9`, is insufficient because percentages and robust scores would be misleading.
- NaN, positive infinity, negative infinity, and non-numeric measure values return `insufficient_evidence`.
- Duplicate timestamps are combined with deterministic stable summation.
- Unsorted rows are sorted before analysis.
- The output `actual`, `expected`, range, percentage, robust score, and threshold must all be finite.
- A two-segment sum-of-squares scan is used for persistence; it is not described as PELT.
- Calendar effects are not applied. `use_calendar` remains contract-compatible only.
- No multiple-testing correction is applied because only one series is analyzed.

### Configurable Thresholds

The `AnomalyAnalyst` constructor exposes:

- `min_history_periods` (default 12)
- `min_observations` (default 30)
- `zero_baseline_tolerance` (default `1e-9`)

The existing algorithm constants remain documented in code: a minimum anomaly threshold of 2.5, a MAD scale factor of 1.4826, a fallback scale of 5% of the baseline or 1.0, and a 0.65 variance-reduction cut for the change-point scan.

## Driver Behavior

`DriverAnalyst` now treats the prepared contribution table as an additive decomposition with explicit guardrails.

### Input Validation

- A period column, numeric value column, and every requested dimension are required.
- Invalid periods and non-numeric values return `insufficient_evidence`.
- NaN and infinity are insufficient.
- Missing or blank requested dimension values are insufficient.
- Duplicate dimension rows are merged deterministically.
- Unsorted rows do not affect ranking or output.
- Negative contribution values remain supported.

### Unsupported Capabilities

The following are deferred and fail closed with `insufficient_evidence` rather than being silently ignored:

- non-`sum` aggregation;
- price/volume/mix decomposition;
- metric-tree traversal;
- operational-indicator co-movement;
- difference-in-differences comparison groups;
- requesting more dimensions than `max_dimensions`.

`n_bootstrap` remains in the shared parameter contract but no bootstrap interval is computed. The interval is a normal approximation over the aggregate row count, not a row-level bootstrap.

### Change and Evidence Rules

- Both windows must contain observations.
- A zero or near-zero total change, by default `abs(change) <= 1e-12`, is insufficient because contribution shares are undefined.
- Contributions are exact current-minus-comparison changes.
- Reconciliation uses stable summation and is checked for finite values.
- Ranking is by descending absolute contribution with a deterministic key tie-break.
- Small cells remain unstable and grade 1; stable cells are capped at grade 2.
- Causal language is bounded: the result describes association and states that it is not causal evidence. The output also states that observed covariation is not proven causality.
- No multiple-testing correction is applied.
- Drivers are single-level; no metric-tree traversal is performed.

### Configurable Thresholds

The `DriverAnalyst` constructor exposes:

- `zero_change_tolerance` (default `1e-12`)
- `small_cell_threshold` (default 5)

The interval uses the existing normal approximation controlled by the shared `alpha` parameter.

## Tests

Run from `backend/`:

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests/test_an1_anomaly_hardening.py tests/test_an1_driver_hardening.py tests/test_analyst_harness.py
```

Also run the architecture guardrails:

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests/test_an1_architecture.py
```

The new anomaly tests cover valid stable and anomalous series, empty input, missing measure columns, the 12-period boundary, non-finite values, zero and near-zero baselines, duplicate and unsorted timestamps, sparse history, incomplete boundary periods, observation thresholds, finite output, and deferred calendar behavior.

The new driver tests cover valid reconciliation and ranking, empty windows, zero and near-zero total change, non-finite values, invalid parameters, missing dimensions, dimension limits, unsupported capabilities, duplicate rows, unsorted rows, deterministic execution, negative contributions, exact reconciliation, conservative small-cell grading, and causal-boundary language.

## Non-Goals

Phase 2 does not:

- change shared contract models or add result fields;
- modify existing tests;
- implement bootstrap resampling;
- implement PELT, FDR, calendar adjustment, price/volume/mix, difference-in-differences, metric trees, or indicator co-movement;
- add scipy, scikit-learn, statsmodels, or any other dependency;
- wire analysts into the orchestrator, service, database, or frontend;
- claim that deferred methods were used.
