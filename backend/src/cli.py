"""Interactive Day-1 console for the query pipeline.

The console accepts read-only SELECT statements directly, runs them through the
guard, the AST validator and the read-only gateway, and renders the result with
Rich. Long-lived commands inspect tables and toggle the validation mode.
"""

from __future__ import annotations

import builtins
from typing import Callable, cast

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import SQLAlchemyError

from answerability import AnswerabilityChecker
from contracts.models import (
    AnswerabilityResult,
    ClarificationResult,
    ExecutionResult,
    GuardResult,
    RepairOutcome,
    SqlCandidate,
    ValidationMode,
    ValidationResult,
)
from gateway.connection import ConnectionManager
from gateway.executor import QueryExecutor
from guard import Guard
from schema_retriever.catalog import SchemaCatalog
from schema_retriever.perceptor import SchemaPerceptor
from sql.generator import SQLGenerationError, SQLGenerator
from sql.repairer import SelfRepairer
from sql.validator import SQLValidator
from trace_logger import TraceLogger, TraceRecord

_MODES = frozenset({"enterprise", "benchmark"})


def _format_cell(value: object) -> str:
    if value is None:
        return "[dim]NULL[/dim]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        rendered = f"{value:,.4f}".rstrip("0").rstrip(".")
        return rendered or "0"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<binary>"
    return escape(str(value))


class CLI:
    """Rich REPL around guard -> validator -> gateway."""

    def __init__(
        self,
        *,
        manager: ConnectionManager,
        guard: Guard | None = None,
        logger: TraceLogger | None = None,
        console: Console | None = None,
        input_func: Callable[[str], str] | None = None,
        mode: ValidationMode = "enterprise",
        max_rows: int = 50,
    ) -> None:
        if mode not in _MODES:
            raise ValueError("mode must be 'enterprise' or 'benchmark'")

        self._manager = manager
        self._guard = guard or Guard()
        self._logger = logger or TraceLogger()
        self._console = console or Console()
        self._read_input = input_func or builtins.input
        self._mode: ValidationMode = mode
        self._tables = self._load_tables()
        self._validator = self._make_validator()
        self._executor = QueryExecutor(manager, max_rows=max_rows)
        self._query_counter = 0
        self._catalog: SchemaCatalog | None = None
        self._answerability: AnswerabilityChecker | None = None
        self._perceptor: SchemaPerceptor | None = None
        self._generator: SQLGenerator | None = None

    @property
    def mode(self) -> ValidationMode:
        return self._mode

    def run(self) -> int:
        self._print_banner()

        while True:
            try:
                line = self._read_input("DS-Agent > ")
            except EOFError:
                self._console.print()
                return 0
            except KeyboardInterrupt:
                self._console.print("\n[dim]Interrupted. Type exit to quit.[/dim]")
                continue

            command = line.strip()
            if not command:
                continue

            lowered = command.lower()
            if lowered in {"exit", "quit"}:
                return 0
            if lowered in {"help", "?"}:
                self._print_help()
                continue
            if lowered == "tables":
                self._show_tables()
                continue
            if lowered == "mode" or lowered.startswith("mode "):
                self._change_mode(command)
                continue
            if lowered == "ask" or lowered.startswith("ask "):
                self._handle_ask(command[3:].strip())
                continue

            self._handle_statement(command)

    def close(self) -> None:
        self._manager.dispose()

    def _load_tables(self) -> tuple[str, ...]:
        try:
            inspector = sqlalchemy_inspect(self._manager.engine)
            return tuple(sorted(inspector.get_table_names()))
        except (SQLAlchemyError, ValueError):
            return ()

    def _make_validator(self) -> SQLValidator:
        return SQLValidator(self._tables, dialect="sqlite", mode=self._mode)

    def _refresh_tables(self) -> None:
        self._tables = self._load_tables()
        self._validator = self._make_validator()

    def _ensure_nl_pipeline(self) -> None:
        if self._catalog is not None:
            return
        catalog = SchemaCatalog.from_manager(self._manager)
        self._catalog = catalog
        self._answerability = AnswerabilityChecker(catalog)
        self._perceptor = SchemaPerceptor(catalog)
        self._generator = SQLGenerator(catalog, dialect="sqlite")

    def _handle_ask(self, question: str) -> None:
        if not question:
            self._console.print("[yellow]Usage: ask <question>[/yellow]")
            return

        guard_result = self._guard.check(question)
        if not guard_result.passed:
            self._log(question=question, guard=guard_result)
            self._print_guard(guard_result)
            return

        try:
            self._ensure_nl_pipeline()
        except (OSError, ValueError, SQLAlchemyError) as exc:
            self._console.print(
                f"[red]Could not build the schema catalog: "
                f"{escape(str(exc))}[/red]"
            )
            return

        assert self._answerability is not None
        assert self._perceptor is not None
        assert self._generator is not None
        assert self._catalog is not None

        answerability = self._answerability.check(question)
        if not answerability.answerable:
            self._log(
                question=question,
                guard=guard_result,
                answerability=answerability,
            )
            self._print_refusal(answerability)
            return

        perception = self._perceptor.perceive(question)
        if perception.requires_clarification:
            clarification = ClarificationResult(
                what_understood=f"I understood the request as: {question}",
                what_needs_clarification=(
                    perception.clarification_reason
                    or "The value match is ambiguous."
                ),
            )
            self._log(
                question=question,
                guard=guard_result,
                answerability=answerability,
            )
            self._print_clarification(clarification)
            return

        try:
            generation = self._generator.generate(
                question,
                linked_values=perception.linked_values,
            )
        except SQLGenerationError as exc:
            clarification = ClarificationResult(
                what_understood=exc.what_understood,
                what_needs_clarification=exc.what_needs_clarification,
            )
            self._log(
                question=question,
                guard=guard_result,
                answerability=answerability,
            )
            self._print_clarification(clarification)
            return

        validator = SQLValidator(
            generation.allowlist.tables,
            dialect="sqlite",
            mode=self._mode,
            columns=[
                f"{column.table}.{column.column}"
                for column in generation.allowlist.columns
            ],
        )
        repairer = SelfRepairer(
            validator=validator,
            executor=self._executor,
            catalog=self._catalog,
        )
        outcome = repairer.run(
            generation.candidate,
            allowlist=generation.allowlist,
            question=question,
            query_id=self._next_query_id(),
        )

        self._log(
            question=question,
            guard=guard_result,
            validation=outcome.validation,
            execution=outcome.execution,
            answerability=answerability,
            candidate=outcome.candidate or generation.candidate,
            repair=outcome,
        )
        self._print_generated(outcome.candidate or generation.candidate)
        if outcome.success and outcome.execution is not None:
            self._print_execution(outcome.execution)
        elif outcome.clarification is not None:
            self._print_clarification(outcome.clarification)

    def _next_query_id(self) -> str:
        self._query_counter += 1
        return f"Q{self._query_counter}"

    def _handle_statement(self, statement: str) -> None:
        guard_result = self._guard.check(statement)
        if not guard_result.passed:
            self._log(question=statement, guard=guard_result)
            self._print_guard(guard_result)
            return

        validation = self._validator.validate(statement)
        if not validation.approved:
            self._log(question=statement, guard=guard_result, validation=validation)
            self._print_validation(validation)
            return

        execution = self._executor.execute(
            statement, query_id=self._next_query_id()
        )
        self._log(
            question=statement,
            guard=guard_result,
            validation=validation,
            execution=execution,
        )
        self._print_execution(execution)

    def _log(
        self,
        *,
        question: str,
        guard: GuardResult,
        validation: ValidationResult | None = None,
        execution: ExecutionResult | None = None,
        answerability: AnswerabilityResult | None = None,
        candidate: SqlCandidate | None = None,
        repair: RepairOutcome | None = None,
    ) -> TraceRecord | None:
        try:
            record = self._logger.log(
                question=question,
                guard=guard,
                validation=validation,
                execution=execution,
                answerability=answerability,
                candidate=candidate,
                repair=repair,
            )
        except (OSError, TypeError, ValueError) as exc:
            self._console.print(
                f"[yellow]trace logging failed: {escape(str(exc))}[/yellow]"
            )
            return None

        self._console.print(f"[dim]trace: {escape(str(record.path))}[/dim]")
        return record

    def _change_mode(self, command: str) -> None:
        parts = command.split()
        if len(parts) != 2 or parts[1].lower() not in _MODES:
            self._console.print(
                "[yellow]Usage: mode <benchmark|enterprise>[/yellow]"
            )
            return

        self._mode = cast(ValidationMode, parts[1].lower())
        self._validator = self._make_validator()
        self._console.print(
            f"[green]Validation mode set to {self._mode}.[/green]"
        )

    def _table_row_count(self, table: str, index: int) -> str:
        quoted = table.replace('"', '""')
        result = self._executor.execute(
            f'SELECT COUNT(*) FROM "{quoted}"', query_id=f"T{index + 1}"
        )
        if result.status == "ok" and result.rows and result.rows[0]:
            return _format_cell(result.rows[0][0])
        return "-"

    def _show_tables(self) -> None:
        self._refresh_tables()
        if not self._tables:
            self._console.print("[yellow]No tables found.[/yellow]")
            return

        table = Table(title=f"Tables ({len(self._tables)})")
        table.add_column("Table")
        table.add_column("Rows", justify="right")
        for index, name in enumerate(self._tables):
            table.add_row(escape(name), self._table_row_count(name, index))

        self._console.print(table)
        self._console.print(
            f"[dim]{len(self._tables)} tables allowlisted "
            f"(validation mode: {self._mode}).[/dim]"
        )

    def _print_banner(self) -> None:
        self._console.print(
            Panel(
                Text(
                    "DS-Agent console\n"
                    f"Datasource: {self._manager.datasource_id} | "
                    f"mode: {self._mode}\n"
                    "Enter a read-only SELECT statement or ask <question>.\n"
                    "Commands: ask, tables, mode <benchmark|enterprise>, "
                    "help, exit"
                ),
                title="DS-Agent",
                border_style="cyan",
            )
        )

    def _print_help(self) -> None:
        self._console.print(
            Panel(
                Text(
                    "ask <question>                 Generate, repair and run SQL\n"
                    "tables                         List tables and row counts\n"
                    "mode <benchmark|enterprise>    Switch validation rules\n"
                    "help                           Show this help\n"
                    "exit / quit                    Leave the console"
                ),
                title="Commands",
                border_style="blue",
            )
        )

    def _print_guard(self, result: GuardResult) -> None:
        lines = [f"Blocked: {result.reason}"]
        if result.matched_rule:
            lines.append(f"Rule: {result.matched_rule}")
        self._console.print(
            Panel(Text("\n".join(lines)), title="Guard", border_style="red")
        )

    def _print_validation(self, result: ValidationResult) -> None:
        table = Table(title=f"Validation failed ({result.validation_mode})")
        table.add_column("ID", justify="right")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Reason")

        for check in result.checks:
            color = {"pass": "green", "fail": "red", "skipped": "dim"}.get(
                check.status, "white"
            )
            reason = check.reason or ""
            if check.offending:
                reason = f"{reason} ({', '.join(check.offending)})".strip()
            table.add_row(
                str(check.id),
                escape(check.name),
                f"[{color}]{check.status}[/{color}]",
                escape(reason),
            )

        self._console.print(table)

    def _print_refusal(self, result: AnswerabilityResult) -> None:
        lines = ["The catalog cannot answer this question."]
        if result.missing:
            lines.append(f"Missing: {', '.join(result.missing)}")
        if result.closest_answerable:
            lines.append(f"Closest answerable: {result.closest_answerable}")
        self._console.print(
            Panel(Text("\n".join(lines)), title="Refusal", border_style="yellow")
        )

    def _print_clarification(self, result: ClarificationResult) -> None:
        self._console.print(
            Panel(
                Text(
                    "What I understood:\n"
                    f"{result.what_understood}\n\n"
                    "What I need:\n"
                    f"{result.what_needs_clarification}"
                ),
                title="Clarification",
                border_style="yellow",
            )
        )

    def _print_generated(self, candidate: SqlCandidate) -> None:
        self._console.print(
            Panel(
                Text(candidate.sql),
                title="Generated SQL",
                border_style="blue",
            )
        )

    def _print_execution(self, result: ExecutionResult) -> None:
        if result.error is not None:
            self._console.print(
                Panel(
                    Text(f"{result.error.kind}: {result.error.message}"),
                    title=f"{result.query_id} {result.status}",
                    border_style="red",
                )
            )
            return

        if not result.columns:
            self._console.print(
                f"[yellow]{result.query_id} returned no columns.[/yellow]"
            )
            return

        table = Table(
            title=(
                f"{result.query_id}: {result.status} | "
                f"{result.row_count} row(s) | {result.duration_ms} ms"
            )
        )
        for column in result.columns:
            table.add_column(
                f"{escape(column.name)}\n[dim]{escape(column.type)}[/dim]",
                overflow="fold",
            )
        for row in result.rows or ():
            table.add_row(*(_format_cell(value) for value in row))

        self._console.print(table)
        if result.truncated:
            self._console.print(
                f"[yellow]Result truncated at {result.row_count} rows.[/yellow]"
            )


__all__ = ["CLI"]
