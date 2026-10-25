"""Evidence ledger and deterministic regex fact checking (EVID-1).

The ledger is the only approved source of numbers for the final narrative. It
turns executed query rows and governed analyst outputs into ``LedgerClaim``
objects, then the fact checker removes any sentence that contains a number not
present in one of those claims.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any, Iterable

from contracts.models import (
    ClaimNumber,
    ClaimSource,
    FactCheckResult,
    FactCheckViolation,
    Ledger,
    LedgerClaim,
    OrchestrationResult,
)

_NUMBER = re.compile(
    r"(?<![\w.,])"
    r"(-?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))"
    r"(%?)"
    r"(?![\w,]|\.\d)"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CAUSAL = re.compile(
    r"\b(?:because of|due to|caused|causes|causing|led to|resulted from|"
    r"drives|drove)\b",
    re.IGNORECASE,
)


def format_number(value: float) -> str:
    """Render a number the same way the deterministic narrator does."""

    if math.isfinite(value) and float(value).is_integer():
        integer = int(value)
        return f"{integer:,}" if abs(integer) >= 1_000 else str(integer)
    precision = 4 if abs(value) >= 0.0001 else 8
    rendered = f"{value:,.{precision}f}".rstrip("0").rstrip(".")
    return rendered or "0"


def _as_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _numbers_in(value: object) -> list[float]:
    found: list[float] = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_numbers_in(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_numbers_in(item))
    else:
        number = _as_number(value)
        if number is not None:
            found.append(number)
    return found


def _claim_numbers(values: Iterable[float], *, unit: str | None = None) -> tuple[ClaimNumber, ...]:
    seen: set[float] = set()
    numbers: list[ClaimNumber] = []
    for value in values:
        key = round(value, 9)
        if key in seen:
            continue
        seen.add(key)
        numbers.append(
            ClaimNumber(
                token=format_number(value),
                value=float(value),
                unit=unit,
            )
        )
    return tuple(numbers)


def _query_claim(
    claim_id: str,
    query_id: str,
    values: list[float],
    row_count: int,
) -> LedgerClaim | None:
    claim_values = list(values)
    if row_count > 0:
        claim_values.append(float(row_count))
    numbers = _claim_numbers(claim_values)
    if not numbers:
        return None
    return LedgerClaim(
        claim_id=claim_id,
        text=f"The governed query {query_id} supplied the cited numeric values.",
        numbers=numbers,
        source=ClaimSource(type="query", id=query_id),
        grade=1,
        kind="fact",
    )


def _analyst_claim(
    claim_id: str,
    source_id: str,
    module: str,
    values: list[float],
) -> LedgerClaim | None:
    numbers = _claim_numbers(values)
    if not numbers:
        return None
    return LedgerClaim(
        claim_id=claim_id,
        text=f"The {module} analyst measured the cited numeric values.",
        numbers=numbers,
        source=ClaimSource(type="query", id=source_id),
        grade=2,
        kind="fact",
    )


class EvidenceLedger:
    """Builds approved claims from query rows and governed analyst outputs."""

    def build(self, run_id: str, outcome: OrchestrationResult) -> Ledger:
        claims: list[LedgerClaim] = []
        next_id = 1

        for execution in outcome.executions:
            values: list[float] = []
            for row in execution.rows or ():
                for cell in row:
                    number = _as_number(cell)
                    if number is not None:
                        values.append(number)
            claim = _query_claim(
                f"C{next_id}",
                execution.query_id,
                values,
                execution.row_count,
            )
            if claim is not None:
                claims.append(claim)
                next_id += 1

        for output in outcome.analyst_outputs:
            if output.status != "ok" or output.result is None:
                continue
            result_values = _numbers_in(output.result.model_dump(mode="json"))
            source_id = (
                output.sources[0]
                if output.sources
                else (outcome.queries[0].query_id if outcome.queries else "Q0")
            )
            claim = _analyst_claim(
                f"C{next_id}",
                source_id,
                output.module,
                result_values,
            )
            if claim is not None:
                claims.append(claim)
                next_id += 1

        return Ledger(
            run_id=run_id,
            claims=tuple(claims),
            dropped=(),
            hypotheses=(),
        )


class FactChecker:
    """Removes narrative sentences containing unapproved numbers."""

    def verify(self, narrative: str | None, ledger: Ledger) -> FactCheckResult:
        if narrative is None or not narrative.strip():
            return FactCheckResult(
                passed=True,
                violations=(),
                repaired_narrative=None,
                action="pass",
            )

        approved = {
            round(number.value, 9)
            for claim in ledger.claims
            for number in claim.numbers
        }
        kept: list[str] = []
        violations: list[FactCheckViolation] = []
        for sentence in _SENTENCE.split(narrative.strip()):
            tokens = list(_NUMBER.finditer(sentence))
            bad = next(
                (
                    match
                    for match in tokens
                    if round(_token_value(match.group(1)), 9) not in approved
                ),
                None,
            )
            if bad is None:
                causal = _CAUSAL.search(sentence)
                if causal is not None and not any(
                    claim.grade >= 4 for claim in ledger.claims
                ):
                    violations.append(
                        FactCheckViolation(
                            sentence=sentence,
                            kind="causal_language_without_grade4",
                            token=causal.group(0),
                        )
                    )
                    continue
                kept.append(sentence)
                continue
            violations.append(
                FactCheckViolation(
                    sentence=sentence,
                    kind="number_not_in_ledger",
                    token=bad.group(0),
                )
            )

        if not violations:
            return FactCheckResult(
                passed=True,
                violations=(),
                repaired_narrative=narrative.strip(),
                action="pass",
            )

        repaired = " ".join(kept).strip() or None
        return FactCheckResult(
            passed=False,
            violations=tuple(violations),
            repaired_narrative=repaired,
            action="repaired" if repaired is not None else "template_fallback",
        )


def _token_value(token: str) -> float:
    return float(token.replace(",", ""))


__all__ = [
    "EvidenceLedger",
    "FactChecker",
    "format_number",
]
