"""Evidence ledger and deterministic fact checking."""

from evidence.fact_checker import verify
from evidence.ledger import EvidenceLedger, FactChecker, format_number

__all__ = ["EvidenceLedger", "FactChecker", "format_number", "verify"]
