"""Entry point for the Day-1 DS-Agent terminal console."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
SRC_DIR = BACKEND_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cli import CLI
from contracts.models import GatewayConfig
from gateway.connection import ConnectionManager
from trace_logger import TraceLogger

DATABASE_PATH = (BACKEND_DIR / ".." / "brazil_ecommerce.sqlite").resolve()
TRACE_DIR = BACKEND_DIR / "traces"


def build_cli() -> CLI:
    config = GatewayConfig(
        datasource_id="brazil-ecommerce",
        dialect="sqlite",
        connection_url=str(DATABASE_PATH),
        timeout_ms=5_000,
    )
    manager = ConnectionManager(config)
    return CLI(
        manager=manager,
        logger=TraceLogger(TRACE_DIR),
        max_rows=50,
    )


def main() -> int:
    cli = build_cli()
    try:
        return cli.run()
    finally:
        cli.close()


if __name__ == "__main__":
    raise SystemExit(main())
