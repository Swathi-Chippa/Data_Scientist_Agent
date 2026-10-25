"""Bounded SQL self-repair with a frozen schema (SQL-11, SQL-13).

``SelfRepairer`` validates and executes the initial candidate, then makes at
most two repair attempts. Every attempt receives the same ``SchemaAllowlist``
that generation produced. ``SQLReflector`` inspects parser, validator and SQLite
error text and applies deterministic corrections (keyword typos, unknown
columns/tables inside the allowlist, missing enterprise row bounds).

If both attempts fail, or if a candidate cannot be repaired safely, the loop
stops and returns a structured clarification instead of guessing.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Protocol

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from contracts.models import (
    ClarificationResult,
    ExecutionResult,
    RepairAttempt,
    RepairError,
    RepairOutcome,
    RepairRequest,
    SchemaAllowlist,
    SqlCandidate,
    ValidationCheck,
    ValidationResult,
)
from schema_retriever.catalog import SchemaCatalog

_SQL_KEYWORDS = frozenset(
    """
    SELECT FROM WHERE GROUP BY ORDER LIMIT OFFSET JOIN INNER LEFT RIGHT OUTER
    FULL CROSS ON AS WITH AND OR HAVING DISTINCT COUNT AVG SUM MIN MAX ASC DESC
    NULL IS NOT IN EXISTS CASE WHEN THEN ELSE END UNION ALL EXCEPT INTERSECT
    """.split()
)

_TOKEN_PATTERN = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1|[A-Za-z_][A-Za-z0-9_]*")
_MISSING_LIMIT_MARKERS = ("explicit limit", "row limit", "limit")


class Reflector(Protocol):
    def reflect(self, request: RepairRequest) -> SqlCandidate | None:
        ...


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left, right).ratio()


class SQLReflector:
    """Deterministic error-driven candidate refinement."""

    def __init__(
        self,
        catalog: SchemaCatalog,
        *,
        dialect: str = "sqlite",
    ) -> None:
        self._catalog = catalog
        self._dialect = dialect

    def reflect(self, request: RepairRequest) -> SqlCandidate | None:
        sql = request.failed_sql
        corrected = self._fix_keywords(
            sql,
            request.allowlist,
            request.reflector_score_threshold,
        )
        changed = corrected != sql
        sql = corrected

        if request.error.kind in ("validation", "runtime"):
            corrected = self._fix_tables(
                sql,
                request.allowlist,
                request.reflector_score_threshold,
            )
            changed = changed or corrected != sql
            sql = corrected

            corrected = self._fix_columns(
                sql,
                request.allowlist,
                request.reflector_score_threshold,
            )
            changed = changed or corrected != sql
            sql = corrected

            if self._needs_limit(request):
                corrected = self._add_limit(sql)
                changed = changed or corrected != sql
                sql = corrected

        if not changed:
            return None

        if _similarity(request.failed_sql, sql) < request.reflector_score_threshold:
            if not self._structurally_corrected(request, sql):
                return None

        return SqlCandidate(
            candidate_id=f"repair-{_sha(sql)}",
            sql=sql,
            dialect=self._dialect,
            origin="repair",
            repair_of=request.failed_candidate_id or "unknown",
        )

    def _fix_keywords(
        self,
        sql: str,
        allowlist: SchemaAllowlist,
        threshold: float,
    ) -> str:
        known = {
            value.lower()
            for value in (*allowlist.tables,)
        }
        known.update(column.column.lower() for column in allowlist.columns)
        known.update(column.table.lower() for column in allowlist.columns)

        def replace(match: re.Match[str]) -> str:
            token = match.group(0)
            if token.startswith(("'", '"')):
                return token
            lowered = token.lower()
            if lowered in known or lowered in {
                keyword.lower() for keyword in _SQL_KEYWORDS
            }:
                return token
            if len(token) < 4:
                return token
            candidates = [
                keyword
                for keyword in _SQL_KEYWORDS
                if _similarity(lowered, keyword.lower()) >= threshold
            ]
            if not candidates:
                return token
            best = max(
                candidates,
                key=lambda keyword: _similarity(lowered, keyword.lower()),
            )
            return best

        return _TOKEN_PATTERN.sub(replace, sql)

    def _fix_tables(
        self,
        sql: str,
        allowlist: SchemaAllowlist,
        threshold: float,
    ) -> str:
        ast = self._parse(sql)
        if ast is None:
            return sql

        allowed = {table.lower() for table in allowlist.tables}
        cte_names = {cte.alias_or_name.lower() for cte in ast.find_all(exp.CTE)}
        changed = False
        for table in ast.find_all(exp.Table):
            name = table.name
            if not name:
                continue
            lowered = name.lower()
            if (not table.db and lowered in cte_names) or lowered in allowed:
                continue
            match = difflib.get_close_matches(
                lowered, sorted(allowed), n=1, cutoff=threshold
            )
            if not match:
                continue
            table.set("this", exp.to_identifier(match[0]))
            changed = True
        return ast.sql(dialect=self._dialect) if changed else sql

    def _fix_columns(
        self,
        sql: str,
        allowlist: SchemaAllowlist,
        threshold: float,
    ) -> str:
        ast = self._parse(sql)
        if ast is None:
            return sql

        allowed_by_table: dict[str, list[str]] = {}
        for column in allowlist.columns:
            allowed_by_table.setdefault(column.table.lower(), []).append(
                column.column
            )
        if not allowed_by_table:
            return sql

        alias_to_table: dict[str, str] = {}
        cte_names = {cte.alias_or_name.lower() for cte in ast.find_all(exp.CTE)}
        for table in ast.find_all(exp.Table):
            if not table.name:
                continue
            if not table.db and table.name.lower() in cte_names:
                continue
            physical = table.name.lower()
            alias_to_table[physical] = physical
            alias_to_table[table.alias_or_name.lower()] = physical

        changed = False
        for column in ast.find_all(exp.Column):
            if isinstance(column.this, exp.Star):
                continue
            name = column.name.lower()
            if not name:
                continue
            qualifier = column.table.lower()
            if qualifier:
                table = alias_to_table.get(qualifier, qualifier)
                candidates = allowed_by_table.get(table, ())
            else:
                candidates = [
                    item
                    for table_columns in allowed_by_table.values()
                    for item in table_columns
                ]
            if not candidates or name in {item.lower() for item in candidates}:
                continue
            match = difflib.get_close_matches(
                name, candidates, n=1, cutoff=threshold
            )
            if not match:
                continue
            column.set("this", exp.to_identifier(match[0]))
            changed = True
        return ast.sql(dialect=self._dialect) if changed else sql

    def _needs_limit(self, request: RepairRequest) -> bool:
        message = request.error.message.lower()
        return any(marker in message for marker in _MISSING_LIMIT_MARKERS)

    def _add_limit(self, sql: str) -> str:
        ast = self._parse(sql)
        if ast is None:
            return sql
        root = ast
        while isinstance(root, exp.Subquery) and root.this is not None:
            root = root.this
        if root.args.get("limit") is not None:
            return sql
        root.set("limit", exp.Limit(expression=exp.Literal.number(100)))
        return ast.sql(dialect=self._dialect)

    def _structurally_corrected(self, request: RepairRequest, sql: str) -> bool:
        return self._needs_limit(request) and "LIMIT" in sql.upper()

    def _parse(self, sql: str) -> exp.Expression | None:
        try:
            return sqlglot.parse_one(sql, read=self._dialect)
        except ParseError:
            return None


class SelfRepairer:
    """Validate, execute and repair one candidate at most twice."""

    def __init__(
        self,
        *,
        validator,
        executor,
        catalog: SchemaCatalog | None = None,
        reflector: Reflector | None = None,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1 or max_attempts > 2:
            raise ValueError("self-repair is capped at 2 attempts")
        if reflector is None:
            if catalog is None:
                raise ValueError(
                    "a catalog is required when no reflector is supplied"
                )
            reflector = SQLReflector(catalog, dialect=validator.dialect)
        self._validator = validator
        self._executor = executor
        self._reflector = reflector
        self._max_attempts = max_attempts

    def run(
        self,
        candidate: SqlCandidate,
        *,
        allowlist: SchemaAllowlist,
        question: str,
        query_id: str = "Q1",
    ) -> RepairOutcome:
        attempts: list[RepairAttempt] = []
        validation = self._validator.validate(candidate.sql)
        execution = (
            self._executor.execute(candidate.sql, query_id=query_id)
            if validation.approved
            else None
        )
        if self._is_success(validation, execution):
            return self._success(candidate, validation, execution, attempts)
        if execution is not None and execution.status == "timeout":
            return self._failure(
                attempts,
                question,
                RepairError(kind="runtime", message="the query timed out"),
            )

        last_error: RepairError | None = None
        for attempt_number in range(1, self._max_attempts + 1):
            if validation.approved:
                if execution is None or execution.error is None:
                    error = RepairError(
                        kind="runtime", message="the query returned an error"
                    )
                else:
                    error = RepairError(
                        kind="runtime", message=execution.error.message
                    )
            else:
                error = self._validation_error(validation)
            last_error = error

            request = RepairRequest(
                attempt=attempt_number,
                failed_sql=candidate.sql,
                failed_candidate_id=candidate.candidate_id,
                error=error,
                allowlist=allowlist,
            )
            refined = self._reflect(request)
            if refined is None:
                attempts.append(
                    RepairAttempt(
                        attempt=attempt_number,
                        request=request,
                        candidate=None,
                    )
                )
                return self._failure(attempts, question, error)

            refined = self._as_repair(refined, candidate)
            validation = self._validator.validate(refined.sql)
            execution = (
                self._executor.execute(refined.sql, query_id=query_id)
                if validation.approved
                else None
            )
            attempts.append(
                RepairAttempt(
                    attempt=attempt_number,
                    request=request,
                    candidate=refined,
                    validation=validation,
                    execution=execution,
                )
            )
            candidate = refined

            if self._is_success(validation, execution):
                return self._success(
                    candidate, validation, execution, attempts
                )
            if execution is not None and execution.status == "timeout":
                return self._failure(
                    attempts,
                    question,
                    RepairError(
                        kind="runtime", message="the query timed out"
                    ),
                )

        return self._failure(
            attempts,
            question,
            last_error
            or RepairError(
                kind="validation", message="the candidate was not valid"
            ),
        )

    def _reflect(self, request: RepairRequest) -> SqlCandidate | None:
        reflect = getattr(self._reflector, "reflect", self._reflector)
        if not callable(reflect):
            raise TypeError("reflector must be callable or expose reflect()")
        return reflect(request)

    @staticmethod
    def _as_repair(
        refined: SqlCandidate, previous: SqlCandidate
    ) -> SqlCandidate:
        updates: dict[str, object] = {
            "origin": "repair",
            "repair_of": previous.candidate_id,
        }
        if not refined.intended_columns:
            updates["intended_columns"] = previous.intended_columns
        if not refined.grain:
            updates["grain"] = previous.grain
        if not refined.uses_edges:
            updates["uses_edges"] = previous.uses_edges
        if refined.origin != "repair" or not refined.repair_of:
            updates["candidate_id"] = f"repair-{_sha(refined.sql)}"
        return refined.model_copy(update=updates)

    @staticmethod
    def _is_success(
        validation: ValidationResult,
        execution: ExecutionResult | None,
    ) -> bool:
        return (
            validation.approved
            and execution is not None
            and execution.status in ("ok", "truncated")
        )

    @staticmethod
    def _validation_error(validation: ValidationResult) -> RepairError:
        failed = [
            check for check in validation.checks if check.status == "fail"
        ]
        reason = "; ".join(
            f"{check.name}: {check.reason or 'failed'}"
            for check in failed
        ) or "validation failed"
        parse_failed = any(
            check.name == "parse" for check in validation.checks
            if check.status == "fail"
        )
        return RepairError(
            kind="parse" if parse_failed else "validation",
            message=reason[:1_000],
        )

    @staticmethod
    def _success(
        candidate: SqlCandidate,
        validation: ValidationResult,
        execution: ExecutionResult,
        attempts: list[RepairAttempt],
    ) -> RepairOutcome:
        return RepairOutcome(
            success=True,
            candidate=candidate,
            validation=validation,
            execution=execution,
            attempts=tuple(attempts),
        )

    @staticmethod
    def _failure(
        attempts: list[RepairAttempt],
        question: str,
        error: RepairError,
    ) -> RepairOutcome:
        return RepairOutcome(
            success=False,
            attempts=tuple(attempts),
            clarification=ClarificationResult(
                what_understood=(
                    f"I understood the request as: {question}"
                    if question.strip()
                    else "I understood the request as a database question."
                ),
                what_needs_clarification=(
                    "I could not repair the SQL safely after "
                    f"{len(attempts)} attempt(s): {error.message}. "
                    "Please clarify the metric, filter, or grain."
                ),
            ),
        )


__all__ = ["Reflector", "SQLReflector", "SelfRepairer"]
