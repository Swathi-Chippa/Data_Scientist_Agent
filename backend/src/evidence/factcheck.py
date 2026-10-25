"""Fact-checker entry point kept separate from ledger construction."""

from contracts.models import FactCheckResult, Ledger
from evidence.ledger import FactChecker

__all__ = ["FactChecker", "verify"]


def verify(narrative: str | None, ledger: Ledger) -> FactCheckResult:
    return FactChecker().verify(narrative, ledger)
