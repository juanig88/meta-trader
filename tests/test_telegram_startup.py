"""Unit tests for Telegram startup message formatting."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bot_mt5 import Config, _telegram_format_startup_message  # noqa: E402


class TestTelegramStartupMessage(unittest.TestCase):
    def test_table(self) -> None:
        cases = [
            {
                "name": "parallel_on_outside_window",
                "cfg": Config(
                    symbols=("EURUSD", "USDJPY"),
                    magic=260414,
                    xauusd_parallel_enabled=True,
                    xauusd_parallel_symbol="XAUUSD",
                    xauusd_parallel_magic=260415,
                    xauusd_parallel_lot=0.01,
                    xauusd_parallel_sl_mode="atr",
                    xauusd_parallel_sl_atr_mult=1.5,
                    xauusd_parallel_tp_rr=2.0,
                    xauusd_parallel_adx_min=25.0,
                    xauusd_parallel_h1_filter=True,
                    xauusd_parallel_start_hour=10,
                    xauusd_parallel_end_hour=13,
                    xauusd_parallel_cooldown_seconds=300,
                    xauusd_parallel_one_trade_per_bar=True,
                ),
                "now": datetime(2026, 8, 5, 15, 0),
                "recon": {"positions_managed": 0, "pending_orders": 0, "positions": []},
                "want_substr": [
                    "XAUUSD parallel: ON",
                    "magic 260415",
                    "10:00–13:00 local (CERRADA)",
                    "ATR×1.50 · RR 2.00",
                    "ADX≥25",
                    "H1=sí",
                    "H4=sí",
                    "Inventario: loop=0 (gestionadas=0) · xauusd=0 · pendientes=0",
                    "Loop: EURUSD, USDJPY · magic 260414",
                ],
                "want_absent": ["XAUUSD parallel: OFF"],
            },
            {
                "name": "parallel_on_inside_window_with_pos",
                "cfg": Config(
                    symbols=("EURUSD",),
                    magic=260414,
                    xauusd_parallel_enabled=True,
                    xauusd_parallel_magic=260415,
                    xauusd_parallel_start_hour=10,
                    xauusd_parallel_end_hour=13,
                ),
                "now": datetime(2026, 8, 5, 11, 30),
                "recon": {
                    "positions_managed": 1,
                    "pending_orders": 2,
                    "positions": [
                        {"magic": 260414},
                        {"magic": 260415},
                        {"magic": 260415},
                    ],
                },
                "want_substr": [
                    "(ABIERTA)",
                    "Inventario: loop=1 (gestionadas=1) · xauusd=2 · pendientes=2",
                ],
                "want_absent": [],
            },
            {
                "name": "parallel_off",
                "cfg": Config(symbols=("GBPUSD",), magic=1, xauusd_parallel_enabled=False),
                "now": datetime(2026, 8, 5, 12, 0),
                "recon": {"positions_managed": 0, "pending_orders": 0, "positions": []},
                "want_substr": ["XAUUSD parallel: OFF", "Loop: GBPUSD · magic 1"],
                "want_absent": ["XAUUSD parallel: ON"],
            },
        ]

        for case in cases:
            with self.subTest(name=case["name"]):
                msg = _telegram_format_startup_message(
                    case["cfg"],
                    pid=15436,
                    recon=case["recon"],
                    now=case["now"],
                )
                self.assertIn("PID 15436", msg)
                for s in case["want_substr"]:
                    self.assertIn(s, msg)
                for s in case["want_absent"]:
                    self.assertNotIn(s, msg)


if __name__ == "__main__":
    unittest.main()
