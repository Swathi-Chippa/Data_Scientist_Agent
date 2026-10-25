"""Deterministic ingress guard (RTE-1, SAFE-4).

Every question passes these rules before routing. There are no model calls and
no I/O here: the same question always produces the same ``GuardResult``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from contracts.models import GuardReason, GuardResult

_EMPTY_RULE = "guard.empty"
_TOO_LONG_RULE = "guard.too_long"
_MAX_QUESTION_LENGTH = 4_000


@dataclass(frozen=True, slots=True)
class GuardRule:
    rule_id: str
    reason: GuardReason
    pattern: re.Pattern[str]

    def matches(self, text: str) -> bool:
        return self.pattern.search(text) is not None


_INJECTION_RULES = (
    GuardRule(
        "guard.injection.override",
        "injection",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:of\s+)?"
            r"(?:the\s+|your\s+)?(?:previous|prior|above|earlier|system)\s+"
            r"(?:instructions?|prompts?|rules?)\b",
            re.IGNORECASE,
        ),
    ),
    GuardRule(
        "guard.injection.reveal",
        "injection",
        re.compile(
            r"\b(?:reveal|show|print|display|repeat|leak|output)\s+"
            r"(?:me\s+)?(?:your\s+(?:system\s+)?(?:prompt|instructions)|"
            r"the\s+system\s+(?:prompt|instructions))\b",
            re.IGNORECASE,
        ),
    ),
    GuardRule(
        "guard.injection.roleplay",
        "injection",
        re.compile(
            r"\b(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you\s+are))\s+"
            r"(?:a|an|the)?\s*(?:system\s+|database\s+)?"
            r"(?:admin(?:istrator)?|dba|developer|root|superuser)\b",
            re.IGNORECASE,
        ),
    ),
    GuardRule(
        "guard.injection.mode",
        "injection",
        re.compile(
            r"\b(?:developer|debug|dan|jailbreak)\s+mode\b|\bjailbreak\b|"
            r"\bsystem\s+prompt\b",
            re.IGNORECASE,
        ),
    ),
)

_WRITE_RULES = (
    GuardRule(
        "guard.stacked_statement",
        "write_attempt",
        re.compile(r";\s*\S|;\s*;", re.IGNORECASE),
    ),
    GuardRule(
        "guard.ddl",
        "write_attempt",
        re.compile(
            r"\b(?:drop|truncate|alter|create|rename)\s+(?:or\s+replace\s+)?"
            r"(?:table|view|database|schema|index|sequence|user|role|function|"
            r"procedure|trigger|extension)\b",
            re.IGNORECASE,
        ),
    ),
    GuardRule(
        "guard.ddl",
        "write_attempt",
        re.compile(
            r"\b(?:grant|revoke)\s+(?:select|insert|update|delete|all|usage|"
            r"connect|create|drop)\b",
            re.IGNORECASE,
        ),
    ),
    GuardRule(
        "guard.dml",
        "write_attempt",
        re.compile(r"\binsert\s+into\b", re.IGNORECASE),
    ),
    GuardRule(
        "guard.dml",
        "write_attempt",
        re.compile(r"\bupdate\s+[`\"\[\]\w.]+\s+set\b", re.IGNORECASE),
    ),
    GuardRule(
        "guard.dml",
        "write_attempt",
        re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    ),
    GuardRule(
        "guard.dml",
        "write_attempt",
        re.compile(r"\bmerge\s+into\b", re.IGNORECASE),
    ),
    GuardRule(
        "guard.dml",
        "write_attempt",
        re.compile(r"\breplace\s+into\b", re.IGNORECASE),
    ),
    GuardRule(
        "guard.bare_write",
        "write_attempt",
        re.compile(
            r"^\s*(?:drop|truncate|insert|update|delete|alter|create|merge|"
            r"grant|revoke)\s*;?\s*$",
            re.IGNORECASE,
        ),
    ),
)

_UNSAFE_RULES = (
    GuardRule(
        "guard.sql_comment",
        "unsafe",
        re.compile(r"--|/\*|\*/"),
    ),
    GuardRule(
        "guard.file_access",
        "unsafe",
        re.compile(
            r"\b(?:read_csv|read_csv_auto|read_parquet|read_json|"
            r"read_json_auto|read_text|read_blob|read_xlsx|load_file|"
            r"pg_read_file|pg_read_binary_file|pg_ls_dir|xp_cmdshell|"
            r"lo_import|lo_export|pragma|vacuum)\b|"
            r"\binto\s+(?:outfile|dumpfile)\b",
            re.IGNORECASE,
        ),
    ),
)

DEFAULT_RULES: tuple[GuardRule, ...] = (
    *_INJECTION_RULES,
    *_WRITE_RULES,
    *_UNSAFE_RULES,
)


class Guard:
    """Rule-based first line of defence for free-text questions."""

    def __init__(
        self,
        rules: Sequence[GuardRule] | None = None,
        *,
        max_length: int = _MAX_QUESTION_LENGTH,
    ) -> None:
        self._rules = tuple(DEFAULT_RULES if rules is None else rules)
        self._max_length = max_length

    @property
    def rules(self) -> tuple[GuardRule, ...]:
        return self._rules

    def check(self, question: str) -> GuardResult:
        if not isinstance(question, str):
            raise TypeError("question must be a string")

        text = question.strip()
        if not text:
            return GuardResult(
                passed=False, reason="unsafe", matched_rule=_EMPTY_RULE
            )
        if len(text) > self._max_length:
            return GuardResult(
                passed=False, reason="unsafe", matched_rule=_TOO_LONG_RULE
            )

        for rule in self._rules:
            if rule.matches(text):
                return GuardResult(
                    passed=False,
                    reason=rule.reason,
                    matched_rule=rule.rule_id,
                )

        return GuardResult(passed=True)


_DEFAULT_GUARD = Guard()


def check_question(question: str) -> GuardResult:
    """Convenience entry point for callers that do not need a custom Guard."""

    return _DEFAULT_GUARD.check(question)


__all__ = ["DEFAULT_RULES", "Guard", "GuardRule", "check_question"]
