from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402


def test_close_position_market_retries_unsupported_filling():
    tick = SimpleNamespace(bid=147.20, ask=147.22)
    fail = SimpleNamespace(retcode=10030, comment="Unsupported filling mode")
    ok = SimpleNamespace(retcode=bot_mt5.mt5.TRADE_RETCODE_DONE, comment="ok")
    with patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=tick):
        with patch.object(bot_mt5.mt5, "order_send", side_effect=[fail, fail, ok]) as send:
            assert bot_mt5._close_position_market("USDJPY", 1, 0.01, "BUY", comment="usd_ladder_bank") is True
    assert send.call_count == 3
