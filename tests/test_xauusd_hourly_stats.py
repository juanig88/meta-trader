"""
Unit tests for XAUUSD parallel hourly breakdown helpers in bot_mt5.py.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402
from bot_mt5 import Config  # noqa: E402


class TestXauusdHourlyStats(unittest.TestCase):
    def setUp(self) -> None:
        bot_mt5._xauusd_hourly_clear_for_tests()

    def tearDown(self) -> None:
        bot_mt5._xauusd_hourly_clear_for_tests()

    def test_record_signal_open_close_happy_path(self) -> None:
        cases = [
            ("signal@10", "signal", datetime(2026, 8, 5, 10, 15), None, None, 10, "signals", 1),
            ("open@10", "open", datetime(2026, 8, 5, 10, 20), None, 32.5, 10, "opens", 1),
            ("close@11", "close", datetime(2026, 8, 5, 11, 5), 0.50, None, 11, "closes", 1),
            ("close@11b", "close", datetime(2026, 8, 5, 11, 40), -0.50, None, 11, "closes", 2),
            ("skip@10", "skip", datetime(2026, 8, 5, 10, 45), None, None, 10, "skips", 1),
        ]
        for name, kind, when, profit, spread, hour, field, want in cases:
            with self.subTest(name=name):
                bot_mt5._xauusd_record_hourly(
                    kind,  # type: ignore[arg-type]
                    when=when,
                    profit=profit,
                    spread_points=spread,
                )
                bucket = bot_mt5._XAUUSD_HOURLY[hour]
                self.assertEqual(int(bucket[field]), want)

        snap = bot_mt5._xauusd_hourly_snapshot(
            Config(xauusd_parallel_start_hour=10, xauusd_parallel_end_hour=13),
            now=datetime(2026, 8, 5, 12, 0),
        )
        self.assertEqual(snap["date"], "2026-08-05")
        self.assertEqual(snap["totals"]["signals"], 1)
        self.assertEqual(snap["totals"]["opens"], 1)
        self.assertEqual(snap["totals"]["closes"], 2)
        self.assertEqual(snap["totals"]["skips"], 1)
        self.assertAlmostEqual(snap["totals"]["pnl"], 0.0, places=4)
        self.assertAlmostEqual(snap["totals"]["spread_avg"], 32.5, places=1)
        self.assertEqual(snap["window"]["signals"], 1)
        self.assertEqual(snap["window"]["opens"], 1)
        self.assertEqual(snap["window"]["closes"], 2)
        self.assertEqual(snap["window"]["skips"], 1)
        self.assertAlmostEqual(snap["window"]["spread_avg"], 32.5, places=1)

    def test_new_day_resets_buckets(self) -> None:
        bot_mt5._xauusd_record_hourly("signal", when=datetime(2026, 8, 5, 12, 0))
        self.assertEqual(bot_mt5._XAUUSD_HOURLY[12]["signals"], 1)
        bot_mt5._xauusd_record_hourly("signal", when=datetime(2026, 8, 6, 9, 0))
        self.assertEqual(bot_mt5._XAUUSD_HOURLY_DAY, "2026-08-06")
        self.assertNotIn(12, bot_mt5._XAUUSD_HOURLY)
        self.assertEqual(bot_mt5._XAUUSD_HOURLY[9]["signals"], 1)

    def test_close_outside_window_flagged(self) -> None:
        bot_mt5._xauusd_record_hourly("close", when=datetime(2026, 8, 5, 15, 10), profit=0.50)
        snap = bot_mt5._xauusd_hourly_snapshot(
            Config(xauusd_parallel_start_hour=10, xauusd_parallel_end_hour=13),
            now=datetime(2026, 8, 5, 16, 0),
        )
        self.assertEqual(snap["totals"]["closes"], 1)
        self.assertEqual(snap["window"]["closes"], 0)
        self.assertAlmostEqual(snap["totals"]["pnl"], 0.50, places=4)
        self.assertAlmostEqual(snap["window"]["pnl"], 0.0, places=4)
        row15 = next(r for r in snap["by_hour"] if r["hour"] == 15)
        self.assertFalse(row15["in_window"])

    def test_multi_session_in_window_flags(self) -> None:
        cfg = Config(xauusd_parallel_sessions="07:00-10:00,14:00-16:00,19:00-23:00")
        hours = [8, 12, 14, 18, 19]
        for h in hours:
            bot_mt5._xauusd_record_hourly("signal", when=datetime(2026, 8, 5, h, 15))
        snap = bot_mt5._xauusd_hourly_snapshot(cfg, now=datetime(2026, 8, 5, 20, 0))
        by_hour = {r["hour"]: r["in_window"] for r in snap["by_hour"]}
        cases = [
            ("morning", 8, True),
            ("gap_midday", 12, False),
            ("afternoon", 14, True),
            ("gap_evening", 18, False),
            ("night", 19, True),
        ]
        for name, hour, want in cases:
            with self.subTest(name=name):
                self.assertEqual(by_hour[hour], want)
        self.assertEqual(snap["window"]["signals"], 3)
        self.assertEqual(snap["totals"]["signals"], 5)
        self.assertIn("14:00", snap["window"]["label"])
        self.assertIn("19:00", snap["window"]["label"])

    def test_invalid_spread_ignored(self) -> None:
        bot_mt5._xauusd_record_hourly(
            "open",
            when=datetime(2026, 8, 5, 10, 0),
            spread_points="bad",  # type: ignore[arg-type]
        )
        bucket = bot_mt5._XAUUSD_HOURLY[10]
        self.assertEqual(bucket["opens"], 1)
        self.assertEqual(bucket["spread_n"], 0)

    def test_empty_snapshot(self) -> None:
        snap = bot_mt5._xauusd_hourly_snapshot(
            Config(xauusd_parallel_start_hour=10, xauusd_parallel_end_hour=14)
        )
        self.assertEqual(snap["by_hour"], [])
        self.assertEqual(snap["totals"]["signals"], 0)
        self.assertEqual(snap["totals"]["skips"], 0)
        self.assertIsNone(snap["totals"]["spread_avg"])

    def test_skip_log_rate_limited_but_counter_always_increments(self) -> None:
        t0 = datetime(2026, 8, 5, 10, 0, 0)
        logged: list[dict] = []

        def fake_log(payload: dict) -> None:
            logged.append(dict(payload))

        with patch.object(bot_mt5, "_log_event", side_effect=fake_log):
            bot_mt5._xauusd_log_skip("XAUUSD", "adx_flat", when=t0, adx=15.0)
            bot_mt5._xauusd_log_skip("XAUUSD", "adx_flat", when=t0 + timedelta(seconds=30), adx=16.0)
            bot_mt5._xauusd_log_skip(
                "XAUUSD",
                "adx_flat",
                when=t0 + timedelta(seconds=301),
                adx=17.0,
            )

        self.assertEqual(len(logged), 2)
        self.assertEqual(logged[0]["event"], "XAUUSD_PARALLEL_SKIP")
        self.assertEqual(logged[0]["reason"], "adx_flat")
        self.assertEqual(bot_mt5._XAUUSD_HOURLY[10]["skips"], 3)
        self.assertEqual(bot_mt5._XAUUSD_SKIP_REASONS.get("adx_flat"), 3)
        self.assertEqual(bot_mt5._XAUUSD_LAST_SKIP.get("reason"), "adx_flat")

    def test_soft_close_dedup(self) -> None:
        t0 = datetime(2026, 8, 5, 11, 0, 0)
        bot_mt5._xauusd_record_soft_close(ticket=111, profit=0.5, when=t0, closed=True)
        bot_mt5._xauusd_record_soft_close(ticket=111, profit=0.5, when=t0, closed=True)
        bot_mt5._xauusd_record_soft_close(ticket=222, profit=-0.5, when=t0, closed=False)
        self.assertEqual(bot_mt5._XAUUSD_HOURLY[11]["closes"], 1)
        self.assertAlmostEqual(bot_mt5._XAUUSD_HOURLY[11]["pnl"], 0.5, places=4)

    def test_cooldown_remaining(self) -> None:
        cfg = Config(xauusd_parallel_cooldown_seconds=300)
        bot_mt5._XAUUSD_LAST_OPEN_AT = datetime(2026, 8, 5, 10, 0, 0)
        left = bot_mt5._xauusd_cooldown_remaining_sec(cfg, datetime(2026, 8, 5, 10, 2, 0))
        self.assertAlmostEqual(left, 180.0, places=1)
        left2 = bot_mt5._xauusd_cooldown_remaining_sec(cfg, datetime(2026, 8, 5, 10, 6, 0))
        self.assertEqual(left2, 0.0)


if __name__ == "__main__":
    unittest.main()
