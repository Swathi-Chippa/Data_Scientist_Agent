"""Post-execution reconciliation checks and optional diagnosis (SQL-10, MET-5)."""

from __future__ import annotations

import json
from collections import Counter
from typing import Iterable, Mapping

from contracts.models import (
    ExecutionResult,
    MetricJoinPath,
    ReconciliationCheck,
    ReconciliationComparison,
    ReconciliationResult,
)
from models.client import ModelResponseError, ModelUnavailableError


class Reconciler:
    """Deterministic checks over an execution result before it is shown."""

    def reconcile(
        self,
        execution: ExecutionResult,
        *,
        expected_grain: Iterable[str] = (),
        join_path: Iterable[MetricJoinPath] = (),
        mandatory_columns: Iterable[str] = (),
        metric_id: str | None = None,
        observed_value: float | None = None,
        registry_expected: float | None = None,
        tolerance: float = 0.0,
        retry_used: bool = False,
        value_ranges: Mapping[str, tuple[float | None, float | None]] | None = None,
    ) -> ReconciliationResult:
        rows = execution.rows or ()
        column_names = tuple(column.name.lower() for column in execution.columns)
        index_by_name = {
            name: index for index, name in enumerate(column_names)
        }
        checks: list[ReconciliationCheck] = []
        caveats: list[str] = []

        grain = tuple(expected_grain)
        join_edges = tuple(join_path)
        grain_columns = [index_by_name.get(name.lower()) for name in grain]
        if grain and any(index is None for index in grain_columns):
            checks.append(
                ReconciliationCheck(
                    name="grain",
                    status="flag",
                    detail="Expected grain column is missing from the result.",
                )
            )
            caveats.append(
                "The expected grain could not be checked because a grain "
                "column is missing."
            )
            duplicate_keys: Counter[tuple[object, ...]] = Counter()
        else:
            duplicate_keys = Counter(
                tuple(row[index] for index in grain_columns)  # type: ignore[index]
                for row in rows
            )
            duplicates = {
                key: count for key, count in duplicate_keys.items() if count > 1
            }
            fanout = any(
                edge.cardinality in ("1:N", "N:M") for edge in join_edges
            )
            if duplicates and fanout:
                checks.append(
                    ReconciliationCheck(
                        name="grain",
                        status="flag",
                        detail="Duplicate grain values suggest join fan-out.",
                    )
                )
                caveats.append(
                    "Possible join fan-out: the approved path is one-to-many "
                    "and the result repeats expected grain values."
                )
            elif duplicates:
                checks.append(
                    ReconciliationCheck(
                        name="grain",
                        status="flag",
                        detail="Expected grain is not unique.",
                    )
                )
                caveats.append(
                    "Expected grain is not unique; one or more grain values repeat."
                )
            else:
                checks.append(
                    ReconciliationCheck(
                        name="grain",
                        status="pass",
                        detail="Expected grain is unique."
                        if grain
                        else "No expected grain supplied.",
                    )
                )

        mandatory = tuple(column.lower() for column in mandatory_columns)
        null_columns: list[str] = []
        for name in mandatory:
            index = index_by_name.get(name)
            if index is None:
                null_columns.append(name)
                continue
            if any(row[index] is None for row in rows):
                null_columns.append(name)
        if null_columns:
            checks.append(
                ReconciliationCheck(
                    name="nulls",
                    status="flag",
                    detail="Null values found in mandatory columns.",
                )
            )
            caveats.append(
                "Null values found in mandatory column(s): "
                + ", ".join(null_columns)
                + "."
            )
        else:
            checks.append(
                ReconciliationCheck(
                    name="nulls",
                    status="pass",
                    detail="No nulls in mandatory columns.",
                )
            )

        duplicate_count = sum(
            count - 1 for count in duplicate_keys.values() if count > 1
        )
        if duplicate_count > 0:
            checks.append(
                ReconciliationCheck(
                    name="duplicates",
                    status="flag",
                    detail=f"{duplicate_count} duplicate row(s) on expected grain.",
                )
            )
            if not any("duplicate" in caveat.lower() for caveat in caveats):
                caveats.append(
                    f"Duplicate rows found on the expected grain "
                    f"({duplicate_count} extra row(s))."
                )
        else:
            checks.append(
                ReconciliationCheck(
                    name="duplicates",
                    status="pass",
                    detail="No duplicate rows on expected grain.",
                )
            )

        range_violations: list[str] = []
        for name, (minimum, maximum) in (value_ranges or {}).items():
            index = index_by_name.get(name.lower())
            if index is None:
                range_violations.append(f"{name} (missing column)")
                continue
            for row in rows:
                value = row[index]
                if value is None:
                    continue
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    range_violations.append(f"{name} (non-numeric value)")
                    continue
                if minimum is not None and numeric < minimum:
                    range_violations.append(f"{name}={numeric} below {minimum}")
                if maximum is not None and numeric > maximum:
                    range_violations.append(f"{name}={numeric} above {maximum}")
        if range_violations:
            checks.append(
                ReconciliationCheck(
                    name="ranges",
                    status="flag",
                    detail="; ".join(range_violations),
                )
            )
            caveats.append(
                "Value range violation: " + "; ".join(range_violations) + "."
            )
        else:
            checks.append(
                ReconciliationCheck(
                    name="ranges",
                    status="pass",
                    detail="All value ranges are within bounds.",
                )
            )

        comparison: ReconciliationComparison | None = None
        if (
            metric_id is not None
            and observed_value is not None
            and registry_expected is not None
        ):
            observed = float(observed_value)
            expected = float(registry_expected)
            within = abs(observed - expected) <= tolerance
            comparison = ReconciliationComparison(
                metric_id=metric_id,
                observed=observed,
                expected=expected,
                tolerance=tolerance,
                within=within,
            )
            if within:
                checks.append(
                    ReconciliationCheck(
                        name="registry_total",
                        status="pass",
                        detail="Observed value is within registry tolerance.",
                    )
                )
            else:
                checks.append(
                    ReconciliationCheck(
                        name="registry_total",
                        status="flag",
                        detail="Observed value is outside registry tolerance.",
                    )
                )
                caveats.append(
                    f"Observed {metric_id}={observed} differs from registry "
                    f"expected={expected} beyond tolerance {tolerance}."
                )
        else:
            checks.append(
                ReconciliationCheck(
                    name="registry_total",
                    status="pass",
                    detail="No registry comparison supplied.",
                )
            )

        status = "flags" if any(check.status == "flag" for check in checks) else "pass"
        return ReconciliationResult(
            status=status,
            checks=tuple(checks),
            registry_comparison=comparison,
            caveats=tuple(dict.fromkeys(caveats)),
            retry_used=retry_used,
        )

    def diagnose(
        self,
        result: ReconciliationResult,
        question: str,
        *,
        client: object | None = None,
    ) -> str | None:
        if result.status != "flags":
            return None
        if client is None:
            return None

        messages = [
            {
                "role": "system",
                "content": (
                    "You explain one reconciliation failure to an analyst. "
                    "Be concise, name the likely cause, and do not write SQL."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "checks": [
                            {
                                "name": check.name,
                                "status": check.status,
                                "detail": check.detail,
                            }
                            for check in result.checks
                            if check.status == "flag"
                        ],
                        "caveats": list(result.caveats),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = client.chat(messages)  # type: ignore[attr-defined]
        except (ModelUnavailableError, ModelResponseError):
            return None
        content = getattr(response, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(response, str) and response.strip():
            return response.strip()
        return None


__all__ = ["Reconciler"]
