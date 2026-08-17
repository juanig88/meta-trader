"""
Multi-session coverage: BTC weekday windows, weekend block, XAU shared risk gates.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402
from bot_mt5 import Config, parse_trading_window_sessions  # noqa: E402


class TestBtcSessionAndWeekend(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config(
            trading_window_enabled=True,
            trading_window_timezone="LOCAL",
            trading_window_sessions=(),
            weekend_block_enabled=True,
            weekend_trade_symbols=(),
            quiet_session_block_enabled=True,
            quiet_session_hours=(0, 1, 2, 3, 4, 5),
            quiet_session_exempt_symbols=(),
            symbols=("BTCUSD",),
            symbol_config={
                "BTCUSD": {
                    "trading_window_sessions": "06:00-09:00,14:00-18:00,21:00-23:00",
                    "adx_min": 22,
                    "cooldown_minutes": 45,
                }
            },
            xauusd_parallel_enabled=False,
        )

    def test_weekend_blocked_even_inside_clock_window(self) -> None:
        sat = datetime(2026, 8, 8, 15, 0)  # Saturday
        self.assertEqual(sat.weekday(), 5)
        self.assertFalse(bot_mt5._session_allows_new_trade(self.cfg, "BTCUSD", sat))

    def test_weekday_inside_session_allowed(self) -> None:
        fri = datetime(2026, 8, 7, 15, 30)  # Friday
        self.assertEqual(fri.weekday(), 4)
        self.assertTrue(bot_mt5._session_allows_new_trade(self.cfg, "BTCUSD", fri))

    def test_weekday_outside_session_blocked(self) -> None:
        fri = datetime(2026, 8, 7, 11, 0)  # Friday 11 — not in BTC windows
        self.assertFalse(bot_mt5._session_allows_new_trade(self.cfg, "BTCUSD", fri))

    def test_quiet_hour_blocked_no_exempt(self) -> None:
        wed = datetime(2026, 8, 5, 4, 30)
        self.assertFalse(bot_mt5._session_allows_new_trade(self.cfg, "BTCUSD", wed))

    def test_btc_windows_parse_three_ranges(self) -> None:
        s = parse_trading_window_sessions("06:00-09:00,14:00-18:00,21:00-23:00")
        self.assertEqual(len(s), 3)
        self.assertEqual(s[0][0], time(6, 0))
        self.assertEqual(s[2][1], time(23, 0))


class TestXauSharedRiskGates(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config(
            xauusd_parallel_enabled=True,
            xauusd_parallel_symbol="XAUUSD",
            xauusd_parallel_start_hour=10,
            xauusd_parallel_end_hour=14,
            xauusd_parallel_max_positions=1,
            daily_loss_limit=-5.0,
            max_open_trades_total=4,
            symbols=("BTCUSD",),
            monthly_loss_limit_enabled=False,
        )
        self._inside = datetime(2026, 8, 7, 11, 0, 0)

    def _run_cycle(self, *, skips: list[str]) -> None:
        def fake_skip(symbol, reason, **kwargs):
            skips.append(str(reason))

        with patch.object(bot_mt5, "datetime") as mock_dt, patch.object(
            bot_mt5, "_xauusd_log_skip", side_effect=fake_skip
        ), patch.object(bot_mt5, "_xauusd_parallel_positions", return_value=[]):
            mock_dt.now.return_value = self._inside
            bot_mt5.run_xauusd_parallel_cycle(self.cfg)

    def test_daily_loss_blocks_parallel_entries(self) -> None:
        skips: list[str] = []
        with patch.object(bot_mt5, "daily_loss_limit_reached", return_value=(True, -9.0)), patch.object(
            bot_mt5, "monthly_loss_limit_reached", return_value=(False, {"enabled": False})
        ), patch.object(bot_mt5, "_count_all_bot_open_positions", return_value=0):
            self._run_cycle(skips=skips)
        self.assertIn("daily_loss_limit", skips)

    def test_max_opens_blocks_parallel_entries(self) -> None:
        skips: list[str] = []
        with patch.object(bot_mt5, "daily_loss_limit_reached", return_value=(False, 0.0)), patch.object(
            bot_mt5, "monthly_loss_limit_reached", return_value=(False, {"enabled": False})
        ), patch.object(bot_mt5, "_count_all_bot_open_positions", return_value=4):
            self._run_cycle(skips=skips)
        self.assertIn("max_open_trades_total", skips)


if __name__ == "__main__":
    unittest.main()
