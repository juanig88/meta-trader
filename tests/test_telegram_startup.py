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
                "name": "rotation_compact",
                "cfg": Config(
                    symbols=tuple(f"S{i}USD!" for i in range(79)),
                    magic=260414,
                    kill_switch_flatten=True,
                    daily_loss_limit=-25.0,
                ),
                "now": datetime(2026, 10, 5, 11, 0),
                "recon": {"positions_managed": 2, "pending_orders": 0, "positions": [{"magic": 260414}] * 2},
                "want_substr": [
                    "rotación crypto demo",
                    "79 símbolos · magic 260414 · tope 60 aperturas/h",
                    "Kill switch armado · flatten on · daily loss -25.00",
                    "Abiertas 2 (gestionadas 2) · pendientes 0",
                ],
                "want_absent": ["XAUUSD", "S0USD!", "Loop:"],
            },
            {
                "name": "rotation_disarmed",
                "cfg": Config(symbols=("A",), magic=1, kill_switch_armed=False),
                "now": datetime(2026, 10, 5, 11, 0),
                "recon": {},
                "want_substr": ["Kill switch DESARMADO · flatten off", "Abiertas 0 (gestionadas 0)"],
                "want_absent": [],
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
