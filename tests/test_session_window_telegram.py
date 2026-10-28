"""
Tests for per-instrument session window Telegram start/end notifications.
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


class TestSessionWindowTelegram(unittest.TestCase):
    def setUp(self) -> None:
        bot_mt5._session_window_prev_clear_for_tests()

    def tearDown(self) -> None:
        bot_mt5._session_window_prev_clear_for_tests()

    def test_parallel_window_inside_exclusive_end(self) -> None:
        cases = [
            ("09:59 out", datetime(2026, 8, 5, 9, 59), False),
            ("10:00 in", datetime(2026, 8, 5, 10, 0), True),
            ("13:59 in", datetime(2026, 8, 5, 13, 59), True),
            ("14:00 out", datetime(2026, 8, 5, 14, 0), False),
        ]
        item = {
            "sessions": ((time(10, 0), time(14, 0)),),
            "end_exclusive": True,
            "tz": "LOCAL",
        }
        cfg = Config()
        for name, wall, want in cases:
            with self.subTest(name=name):
                self.assertEqual(bot_mt5._instrument_session_inside(item, cfg, wall), want)

    def test_symbol_window_5_to_12(self) -> None:
        cfg = Config(
            trading_window_enabled=True,
            trading_window_timezone="LOCAL",
            trading_window_sessions=(),
            symbols=("NVDA.NAS",),
            symbol_config={"NVDA.NAS": {"trading_window_sessions": "05:00-12:00"}},
        )
        windows = bot_mt5._instrument_session_windows(cfg)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["symbol"], "NVDA.NAS")
        self.assertEqual(windows[0]["range_label"], "05:00–12:00")
        self.assertTrue(
            bot_mt5._instrument_session_inside(windows[0], cfg, datetime(2026, 8, 5, 5, 0))
        )
        self.assertTrue(
            bot_mt5._instrument_session_inside(windows[0], cfg, datetime(2026, 8, 5, 12, 0))
        )
        self.assertFalse(
            bot_mt5._instrument_session_inside(windows[0], cfg, datetime(2026, 8, 5, 12, 1))
        )

    def test_notify_start_and_end_edges(self) -> None:
        cfg = Config(
            trading_window_enabled=True,
            trading_window_timezone="LOCAL",
            trading_window_sessions=(),
            symbols=("NVDA.NAS",),
            symbol_config={"NVDA.NAS": {"trading_window_sessions": "10:00-14:00"}},
        )
        sent: list[str] = []

        def fake_notify(text: str, **kwargs) -> None:
            sent.append(text)

        with patch.object(bot_mt5.telegram_notify, "telegram_configured", return_value=True), patch.object(
            bot_mt5.telegram_notify, "env_bool", return_value=True
        ), patch.object(bot_mt5.telegram_notify, "notify", side_effect=fake_notify):
            # First poll at 09:50 — seed only
            bot_mt5._telegram_notify_session_windows(cfg, datetime(2026, 8, 5, 9, 50))
            self.assertEqual(sent, [])
            # Cross into window
            bot_mt5._telegram_notify_session_windows(cfg, datetime(2026, 8, 5, 10, 0))
            self.assertEqual(len(sent), 1)
            self.assertIn("inicio de sesión", sent[0])
            self.assertIn("NVDA.NAS", sent[0])
            # Still inside — no extra message
            bot_mt5._telegram_notify_session_windows(cfg, datetime(2026, 8, 5, 11, 0))
            self.assertEqual(len(sent), 1)
            # Leave window
            bot_mt5._telegram_notify_session_windows(cfg, datetime(2026, 8, 5, 14, 1))
            self.assertEqual(len(sent), 2)
            self.assertIn("fin de sesión", sent[1])

    def test_format_session_ranges(self) -> None:
        s = parse_trading_window_sessions("05:00-12:00,13:00-18:00")
        self.assertEqual(bot_mt5._format_session_ranges(s), "05:00–12:00, 13:00–18:00")

    def test_legacy_hours_notify_uses_local_even_if_cfg_utc(self) -> None:
        """
        Bug fix: con TIMEZONE=UTC, Telegram no debe cerrar a las 13 ART (16 UTC)
        la ventana legacy 11–16, porque el gate de trading usa hora LOCAL.
        """
        cfg = Config(
            trading_window_enabled=True,
            trading_window_timezone="UTC",
            trading_window_sessions=(),
            start_hour=11,
            end_hour=16,
            symbols=("EURUSD", "USDJPY"),
        )
        windows = bot_mt5._instrument_session_windows(cfg)
        self.assertEqual(len(windows), 2)
        for w in windows:
            self.assertEqual(w["tz"], "LOCAL")
            self.assertTrue(w.get("legacy_hour_inclusive"))
            self.assertEqual(w["range_label"], "11:00–16:00")

        cases = [
            ("10:59 out", datetime(2026, 8, 6, 10, 59), False),
            ("11:00 in", datetime(2026, 8, 6, 11, 0), True),
            ("13:00 still in (ART)", datetime(2026, 8, 6, 13, 0), True),
            ("16:30 still in (hour inclusive)", datetime(2026, 8, 6, 16, 30), True),
            ("17:00 out", datetime(2026, 8, 6, 17, 0), False),
        ]
        for name, wall, want in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    bot_mt5._instrument_session_inside(windows[0], cfg, wall),
                    want,
                )

        sent: list[str] = []

        def fake_notify(text: str, **kwargs) -> None:
            sent.append(text)

        with patch.object(bot_mt5.telegram_notify, "telegram_configured", return_value=True), patch.object(
            bot_mt5.telegram_notify, "env_bool", return_value=True
        ), patch.object(bot_mt5.telegram_notify, "notify", side_effect=fake_notify):
            bot_mt5._telegram_notify_session_windows(cfg, datetime(2026, 8, 6, 12, 0))
            self.assertEqual(sent, [])
            # 13:00 ART must NOT end the session
            bot_mt5._telegram_notify_session_windows(cfg, datetime(2026, 8, 6, 13, 0))
            self.assertEqual(sent, [])
            # End only after local hour > 16
            bot_mt5._telegram_notify_session_windows(cfg, datetime(2026, 8, 6, 17, 0))
            self.assertEqual(len(sent), 2)  # EURUSD + USDJPY
            self.assertTrue(all("fin de sesión" in m for m in sent))
            self.assertTrue(all("(local)" in m for m in sent))
            self.assertTrue(all("(UTC)" not in m for m in sent))

    def test_utc_caption_includes_local_equivalent(self) -> None:
        item = {
            "sessions": ((time(13, 30), time(20, 0)),),
            "tz": "UTC",
            "range_label": "13:30–20:00",
        }
        with patch.object(bot_mt5, "_local_utc_offset_hours", return_value=-3):
            caption = bot_mt5._session_window_range_caption(item, datetime(2026, 8, 6, 12, 0))
        self.assertIn("13:30–20:00 (UTC)", caption)
        self.assertIn("10:30–17:00 (local)", caption)


if __name__ == "__main__":
    unittest.main()
