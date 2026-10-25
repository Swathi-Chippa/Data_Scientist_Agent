"""SQLGlot AST validator (SAFE-1, SAFE-2, SQL-2 to SQL-7, SQL-17).

Core gates are always active: one read-only statement, no DML/DDL/file access
or system catalogs, and every physical table must be in the retrieved allowlist.
Enterprise mode additionally requires explicit projections and a bounded result;
benchmark mode relaxes only those two rules so BIRD gold result sets survive.
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from contracts.models import ValidationCheck, ValidationMode, ValidationResult

_READ_ONLY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

_FORBIDDEN_NODE_NAMES = frozenset(
    {
        "Insert",
        "Update",
        "Delete",
        "Create",
        "Drop",
        "Alter",
        "Truncate",
        "TruncateTable",
        "Merge",
        "Grant",
        "Revoke",
        "Command",
        "Transaction",
        "Commit",
        "Rollback",
        "Attach",
        "Detach",
        "Pragma",
        "Copy",
        "LoadData",
        "Export",
        "Use",
        "Set",
    }
)

_BANNED_FUNCTIONS = frozenset(
    {
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "read_text",
        "read_blob",
        "read_xlsx",
        "load_file",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "xp_cmdshell",
        "lo_import",
        "lo_export",
        "openrowset",
        "opendatasource",
        "duckdb_tables",
        "duckdb_columns",
        "duckdb_views",
        "duckdb_indexes",
        "duckdb_settings",
    }
)

_SYSTEM_SCHEMAS = frozenset(
    {"information_schema", "pg_catalog", "performance_schema", "mysql", "sys"}
)

_SYSTEM_TABLES = frozenset(
    {
        "sqlite_master",
        "sqlite_schema",
        "sqlite_temp_master",
        "duckdb_tables",
        "duckdb_columns",
        "duckdb_views",
        "duckdb_indexes",
        "duckdb_settings",
    }
)

_COMMON_SCHEMAS = frozenset({"main", "public", "dbo"})

_COMMENT_PATTERN = re.compile(r"--|/\*|\*/")


def _normalize_identifier(name: str) -> str:
    return name.strip().strip('`"[]').lower()


def _unwrap_root(expression: exp.Expression) -> exp.Expression:
    while isinstance(expression, exp.Subquery) and expression.this is not None:
        expression = expression.this
    return expression


def _function_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    if isinstance(node, exp.Func):
        return node.sql_name().lower()
    return None


def _physical_tables(ast: exp.Expression) -> Iterator[exp.Table]:
    cte_names = {cte.alias_or_name.lower() for cte in ast.find_all(exp.CTE)}

    for table in ast.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        if not table.db and name.lower() in cte_names:
            continue
        yield table


def _table_label(table: exp.Table) -> str:
    return ".".join(part for part in (table.catalog, table.db, table.name) if part)


def _star_projections(ast: exp.Expression) -> tuple[str, ...]:
    stars: list[str] = []
    for select in ast.find_all(exp.Select):
        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                stars.append(projection.sql())
            elif isinstance(projection, exp.Column) and isinstance(
                projection.this, exp.Star
            ):
                stars.append(projection.sql())
    return tuple(stars)


def _has_row_bound(ast: exp.Expression) -> bool:
    root = _unwrap_root(ast)
    return any(
        root.args.get(key) is not None for key in ("limit", "fetch", "top")
    )


class SQLValidator:
    """Deterministic validator built on the SQLGlot AST."""

    def __init__(
        self,
        allowlist: Iterable[str] | str,
        *,
        dialect: str = "duckdb",
        mode: ValidationMode = "enterprise",
    ) -> None:
        if isinstance(allowlist, str):
            allowlist = (allowlist,)
        if mode not in ("enterprise", "benchmark"):
            raise ValueError("mode must be 'enterprise' or 'benchmark'")

        sqlglot.Dialect.get_or_raise(dialect)

        self._allowlist = frozenset(
            _normalize_identifier(name) for name in allowlist
        )
        self.dialect = dialect
        self.mode = mode

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allowlist

    def validate(self, sql: str) -> ValidationResult:
        checks: list[ValidationCheck] = []

        if not sql.strip():
            checks.append(
                ValidationCheck(
                    id=1, name="parse", status="fail", reason="SQL is empty"
                )
            )
            return self._result(checks)

        try:
            statements = [
                statement
                for statement in sqlglot.parse(sql, read=self.dialect)
                if statement is not None
            ]
        except ParseError as exc:
            reason = str(exc).splitlines()[0][:300]
            checks.append(
                ValidationCheck(
                    id=1, name="parse", status="fail", reason=reason
                )
            )
            return self._result(checks)

        checks.append(ValidationCheck(id=1, name="parse", status="pass"))

        if len(statements) != 1:
            checks.append(
                ValidationCheck(
                    id=2,
                    name="single_select",
                    status="fail",
                    reason=f"expected exactly one statement, found {len(statements)}",
                )
            )
            return self._result(checks)

        checks.append(ValidationCheck(id=2, name="single_select", status="pass"))

        ast = statements[0]
        root = _unwrap_root(ast)
        forbidden_nodes = sorted(
            {
                type(node).__name__
                for node in ast.walk()
                if type(node).__name__ in _FORBIDDEN_NODE_NAMES
            }
        )
        banned_functions = sorted(
            {
                name
                for node in ast.walk()
                if (name := _function_name(node)) in _BANNED_FUNCTIONS
            }
        )
        system_tables = sorted(
            {
                _table_label(table)
                for table in ast.find_all(exp.Table)
                if (table.db or "").lower() in _SYSTEM_SCHEMAS
                or table.name.lower() in _SYSTEM_TABLES
            }
        )

        core_problems: list[str] = []
        if not isinstance(root, _READ_ONLY_ROOTS):
            core_problems.append(
                f"root must be a read-only SELECT, got {type(root).__name__}"
            )
        if forbidden_nodes:
            core_problems.append(
                "write or administrative statements are forbidden "
                f"({', '.join(forbidden_nodes)})"
            )
        if banned_functions:
            core_problems.append(
                "file-reading or system functions are forbidden "
                f"({', '.join(banned_functions)})"
            )
        if system_tables:
            core_problems.append(
                "system catalogs are forbidden "
                f"({', '.join(system_tables)})"
            )
        if _COMMENT_PATTERN.search(sql):
            core_problems.append("SQL comments are not allowed")

        core_ok = not core_problems
        if core_ok:
            checks.append(ValidationCheck(id=3, name="no_dml_ddl", status="pass"))
        else:
            offending = tuple(
                sorted(set(forbidden_nodes) | set(banned_functions) | set(system_tables))
            )
            checks.append(
                ValidationCheck(
                    id=3,
                    name="no_dml_ddl",
                    status="fail",
                    reason="; ".join(core_problems),
                    offending=offending or None,
                )
            )

        unknown_tables = sorted(
            {
                _table_label(table)
                for table in _physical_tables(ast)
                if not self._table_is_allowed(table)
            }
        )
        if unknown_tables:
            checks.append(
                ValidationCheck(
                    id=4,
                    name="allowlist",
                    status="fail",
                    reason="table is outside the retrieved allowlist",
                    offending=tuple(unknown_tables),
                )
            )
        else:
            checks.append(ValidationCheck(id=4, name="allowlist", status="pass"))

        checks.append(
            ValidationCheck(
                id=5,
                name="approved_joins_fanout",
                status="skipped",
                reason="no join graph supplied to this validator",
            )
        )

        checks.append(self._limits_check(ast, root, core_ok))

        return self._result(checks)

    def _table_is_allowed(self, table: exp.Table) -> bool:
        bare = table.name.lower()
        schema = (table.db or "").lower()
        full = ".".join(
            part for part in (table.catalog, table.db, table.name) if part
        ).lower()

        if full in self._allowlist:
            return True
        if not schema and any(
            entry.endswith(f".{bare}") for entry in self._allowlist
        ):
            return True
        return bare in self._allowlist and (
            not schema or schema in _COMMON_SCHEMAS
        )

    def _limits_check(
        self,
        ast: exp.Expression,
        root: exp.Expression,
        core_ok: bool,
    ) -> ValidationCheck:
        if not core_ok:
            return ValidationCheck(
                id=6,
                name="limits_bounds",
                status="skipped",
                reason="not evaluated because the statement is not a read-only SELECT",
            )

        if self.mode == "benchmark":
            return ValidationCheck(
                id=6,
                name="limits_bounds",
                status="skipped",
                reason="benchmark mode relaxes row limits and SELECT * restrictions",
            )

        problems: list[str] = []
        offending: list[str] = []

        stars = _star_projections(ast)
        if stars:
            problems.append("SELECT * is not allowed in enterprise mode")
            offending.extend(stars)
        if not _has_row_bound(root):
            problems.append("enterprise queries require an explicit LIMIT")

        if problems:
            return ValidationCheck(
                id=6,
                name="limits_bounds",
                status="fail",
                reason="; ".join(problems),
                offending=tuple(offending) or None,
            )

        return ValidationCheck(id=6, name="limits_bounds", status="pass")

    def _result(self, checks: list[ValidationCheck]) -> ValidationResult:
        return ValidationResult(
            approved=all(check.status != "fail" for check in checks),
            validation_mode=self.mode,
            checks=tuple(checks),
        )


def validate_sql(
    sql: str,
    allowlist: Iterable[str] | str,
    *,
    dialect: str = "duckdb",
    mode: ValidationMode = "enterprise",
) -> ValidationResult:
    """Convenience wrapper around :class:`SQLValidator`."""

    return SQLValidator(allowlist, dialect=dialect, mode=mode).validate(sql)


__all__ = ["SQLValidator", "validate_sql"]
