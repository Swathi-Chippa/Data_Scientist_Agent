"""End-to-end smoke test for the Day-1 terminal console."""

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from cli import CLI
from contracts.models import GatewayConfig
from gateway.connection import ConnectionManager
from trace_logger import TraceLogger

BACKEND_DIR = Path(__file__).resolve().parents[1]
DB_PATH = (BACKEND_DIR / ".." / "brazil_ecommerce.sqlite").resolve()


def test_cli_runs_end_to_end_without_crashing(monkeypatch, tmp_path):
    responses = iter(
        [
            "SELECT count(*) FROM orders",
            "mode benchmark",
            "SELECT count(*) FROM orders",
            "SELECT * FROM orders",
            "tables",
            "DROP TABLE orders",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        color_system=None,
        width=160,
    )
    manager = ConnectionManager(
        GatewayConfig(
            datasource_id="brazil-ecommerce",
            dialect="sqlite",
            connection_url=str(DB_PATH),
            timeout_ms=5_000,
        )
    )
    trace_dir = tmp_path / "traces"
    cli = CLI(
        manager=manager,
        logger=TraceLogger(trace_dir),
        console=console,
        max_rows=5,
    )

    try:
        exit_code = cli.run()
    finally:
        cli.close()

    rendered = output.getvalue()
    assert exit_code == 0
    assert "DS-Agent" in rendered
    assert "benchmark" in rendered.lower()
    assert "99,441" in rendered
    assert "orders" in rendered
    assert "truncated" in rendered.lower()

    traces = sorted(trace_dir.glob("run_*.json"))
    assert len(traces) == 4
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in traces]

    blocked = [payload for payload in payloads if not payload["guard"]["passed"]]
    assert len(blocked) == 1
    assert blocked[0]["execution"] is None
    assert blocked[0]["guard"]["reason"] == "write_attempt"

    failed_validation = [
        payload
        for payload in payloads
        if payload["validation"] is not None
        and not payload["validation"]["approved"]
    ]
    assert len(failed_validation) == 1
    assert failed_validation[0]["execution"] is None

    truncated = [
        payload
        for payload in payloads
        if payload["execution"] is not None
        and payload["execution"]["status"] == "truncated"
    ]
    assert len(truncated) == 1


def test_cli_answers_a_natural_language_question(monkeypatch, tmp_path):
    responses = iter(
        [
            "ask What is the average review score by customer state?",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        color_system=None,
        width=180,
    )
    manager = ConnectionManager(
        GatewayConfig(
            datasource_id="brazil-ecommerce",
            dialect="sqlite",
            connection_url=str(DB_PATH),
            timeout_ms=5_000,
        )
    )
    trace_dir = tmp_path / "traces"
    cli = CLI(
        manager=manager,
        logger=TraceLogger(trace_dir),
        console=console,
        max_rows=5,
    )

    try:
        exit_code = cli.run()
    finally:
        cli.close()

    rendered = output.getvalue()
    assert exit_code == 0
    assert "avg_review_score" in rendered

    traces = sorted(trace_dir.glob("run_*.json"))
    assert len(traces) == 1
    payload = json.loads(traces[0].read_text(encoding="utf-8"))
    assert payload["guard"]["passed"] is True
    assert payload["validation"]["approved"] is True
    assert payload["execution"]["status"] in {"ok", "truncated"}
    assert payload["execution"]["row_count"] > 0
    assert payload["candidate"]["origin"] == "generator"
