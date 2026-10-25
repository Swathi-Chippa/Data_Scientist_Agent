from __future__ import annotations

from types import SimpleNamespace

from contracts.models import (
    ClaimNumber,
    ClaimSource,
    ExecutionColumn,
    ExecutionResult,
    Ledger,
    LedgerClaim,
)
from evidence.ledger import EvidenceLedger, FactChecker, format_number


def _ledger(*values: float) -> Ledger:
    numbers = tuple(
        ClaimNumber(token=format_number(value), value=value)
        for value in values
    )
    return Ledger(
        run_id="run-1",
        claims=(
            LedgerClaim(
                claim_id="C1",
                text="The governed query supplied the cited values.",
                numbers=numbers,
                source=ClaimSource(type="query", id="Q1"),
                grade=1,
                kind="fact",
            ),
        ),
    )


def test_fact_checker_accepts_commas_and_decimals() -> None:
    result = FactChecker().verify(
        "The result is 6,512 at 0.5 percent.",
        _ledger(6512, 0.5),
    )

    assert result.passed is True
    assert result.action == "pass"
    assert result.violations == ()
    assert result.repaired_narrative == "The result is 6,512 at 0.5 percent."


def test_fact_checker_strips_sentence_with_unapproved_number() -> None:
    result = FactChecker().verify(
        "The result is 6,512. The hidden total is 999.",
        _ledger(6512),
    )

    assert result.passed is False
    assert result.action == "repaired"
    assert result.repaired_narrative == "The result is 6,512."
    assert len(result.violations) == 1
    assert result.violations[0].token == "999"
    assert result.violations[0].kind == "number_not_in_ledger"


def test_fact_checker_returns_none_when_every_sentence_is_removed() -> None:
    result = FactChecker().verify(
        "A hallucinated 999 appeared.",
        _ledger(6512),
    )

    assert result.passed is False
    assert result.action == "template_fallback"
    assert result.repaired_narrative is None


def test_fact_checker_blocks_causal_language_without_grade_four() -> None:
    result = FactChecker().verify(
        "Orders rose because of the promotion.",
        _ledger(6512),
    )

    assert result.passed is False
    assert result.action == "template_fallback"
    assert result.repaired_narrative is None
    assert result.violations[0].kind == "causal_language_without_grade4"
    assert result.violations[0].token == "because of"


def test_fact_checker_passes_empty_narrative() -> None:
    result = FactChecker().verify(None, _ledger(6512))

    assert result.passed is True
    assert result.repaired_narrative is None


def test_evidence_ledger_collects_query_and_row_count_numbers() -> None:
    execution = ExecutionResult(
        query_id="Q1",
        status="ok",
        columns=(ExecutionColumn(name="order_count", type="integer"),),
        row_count=1,
        rows=((6512,),),
        duration_ms=3,
    )
    outcome = SimpleNamespace(
        executions=(execution,),
        analyst_outputs=(),
        queries=(),
    )

    ledger = EvidenceLedger().build("run-1", outcome)

    assert len(ledger.claims) == 1
    claim = ledger.claims[0]
    assert claim.source.id == "Q1"
    assert claim.grade == 1
    assert [number.value for number in claim.numbers] == [6512.0, 1.0]


def test_evidence_ledger_skips_outputs_without_numbers() -> None:
    execution = ExecutionResult(
        query_id="Q1",
        status="ok",
        columns=(ExecutionColumn(name="status", type="text"),),
        row_count=0,
        rows=None,
        duration_ms=1,
    )
    outcome = SimpleNamespace(
        executions=(execution,),
        analyst_outputs=(),
        queries=(),
    )

    ledger = EvidenceLedger().build("run-1", outcome)

    assert ledger.claims == ()
