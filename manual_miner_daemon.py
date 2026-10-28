"""Host-side worker for the manual-trade journal.

MetaTrader 5's Python API is Windows-only and talks to the local terminal, so
this process stays on the host next to bot_mt5.py. It is not a Docker service
and it does not import the order path.

Postgres and Redis still come from infra/docker-compose.yml. This daemon only
reads XBTFX history and writes manual_trade_journal / manual_miner_reports.

Usage:
    py manual_miner_daemon.py
    make miner
"""

from __future__ import annotations

import logging
import sys
from typing import Optional, Sequence


LOG_FILE = "manual_miner.log"


def _attach_log_file(filename: str = LOG_FILE) -> None:
    root = logging.getLogger()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    try:
        handler = logging.FileHandler(filename, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(fmt)
    root.addHandler(handler)


def main(argv: Optional[Sequence[str]] = None) -> int:
    _attach_log_file()
    from trading.application.manual_pattern_miner import main as miner_main

    args = ["--follow"]
    if argv:
        args.extend(str(a) for a in argv)
    return miner_main(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
