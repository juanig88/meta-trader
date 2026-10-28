"""
Multi-session coverage: BTC weekday windows, weekend block.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, time
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
