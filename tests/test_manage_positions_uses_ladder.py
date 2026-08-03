from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Repo root on path (Windows / POSIX)
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402


class TestManagePositionsUsesUsdLadder(unittest.TestCase):
    @patch.object(bot_mt5, "_apply_usd_sl_ladder")
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=SimpleNamespace(bid=1.1, ask=1.1001))
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=SimpleNamespace(digits=5, point=0.0001))
    @patch.object(bot_mt5.mt5, "positions_get")
    @patch.object(bot_mt5, "_position_is_managed_by_bot", return_value=True)
    def test_manage_positions_calls_ladder_when_enabled(
        self,
        _is_managed,
        positions_get,
        _si,
        _st,
        apply_ladder,
    ):
        cfg = bot_mt5.Config(symbols=("EURUSD",), usd_sl_ladder_enabled=True)
        p = SimpleNamespace(
            symbol="EURUSD",
            profit=1.23,
            type=int(bot_mt5.mt5.POSITION_TYPE_BUY),
            volume=0.08,
            time=0,
            price_open=1.1000,
            ticket=123,
            sl=0.0,
            tp=0.0,
        )
        positions_get.return_value = [p]

        with patch.object(bot_mt5, "_effective_params", return_value={"break_even_trigger_money": 0.5, "trail_trigger_money": 1.0, "trail_new_sl_money": 0.5, "trail_new_tp_money": 1.2, "adx_min": 20.0}):
            bot_mt5.manage_positions(cfg)

        apply_ladder.assert_called()


if __name__ == "__main__":
    unittest.main()

