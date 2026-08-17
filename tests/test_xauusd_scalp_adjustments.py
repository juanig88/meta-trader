"""
Unit tests for XAUUSD parallel adjustments: EMA cross edge + SL/TP from fill.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402
from bot_mt5 import (  # noqa: E402
    _ema_series,
    _xauusd_compute_sl_tp_prices,
    _xauusd_ema_cross_decision,
    _xauusd_exit_distances_from_atr,
    _xauusd_h1_bias_allows,
    _xauusd_h4_bias_allows,
)


class TestXauusdEmaCross(unittest.TestCase):
    def test_cross_table(self) -> None:
        cases = [
            ("bullish_cross", 10.0, 11.0, 12.0, 11.5, "BUY"),
            ("bearish_cross", 12.0, 11.0, 10.0, 10.5, "SELL"),
            ("state_bull_no_cross", 12.0, 11.0, 13.0, 11.5, "NONE"),
            ("state_bear_no_cross", 10.0, 11.0, 9.0, 10.5, "NONE"),
            ("equal_to_bull", 10.0, 10.0, 11.0, 10.0, "BUY"),
            ("equal_to_bear", 10.0, 10.0, 9.0, 10.0, "SELL"),
            ("still_equal", 10.0, 10.0, 10.0, 10.0, "NONE"),
        ]
        for name, fp, sp, fc, sc, want in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    _xauusd_ema_cross_decision(fp, sp, fc, sc),
                    want,
                )


class TestXauusdSlTpFromFill(unittest.TestCase):
    def test_buy_sell_and_buffer(self) -> None:
        cases = [
            ("buy_no_buf", "BUY", 2000.0, 2.0, 2.0, 2, 0.0, 1998.0, 2002.0),
            ("buy_with_buf", "BUY", 2000.0, 2.0, 2.0, 2, 0.5, 1997.5, 2002.0),
            ("sell_no_buf", "SELL", 2000.0, 2.0, 2.0, 2, 0.0, 2002.0, 1998.0),
            ("sell_with_buf", "SELL", 2000.0, 2.0, 2.0, 2, 0.5, 2002.5, 1998.0),
            ("buy_slippage_fill", "BUY", 2000.3, 2.0, 2.0, 2, 0.0, 1998.3, 2002.3),
        ]
        for name, side, entry, sl_d, tp_d, digits, buf, want_sl, want_tp in cases:
            with self.subTest(name=name):
                sl, tp = _xauusd_compute_sl_tp_prices(
                    side=side,
                    entry=entry,
                    sl_dist=sl_d,
                    tp_dist=tp_d,
                    digits=digits,
                    spread_buffer=buf,
                )
                self.assertAlmostEqual(sl, want_sl, places=5)
                self.assertAlmostEqual(tp, want_tp, places=5)


class TestEmaSeries(unittest.TestCase):
    def test_warm_and_length(self) -> None:
        values = [float(i) for i in range(1, 31)]
        series = _ema_series(values, 9)
        self.assertEqual(len(series), 30)
        self.assertIsNone(series[7])
        self.assertIsNotNone(series[8])
        self.assertIsNotNone(series[-1])
        # Last value should match _ema on full series
        self.assertAlmostEqual(series[-1], bot_mt5._ema(values, 9), places=8)

    def test_short_series(self) -> None:
        series = _ema_series([1.0, 2.0, 3.0], 9)
        self.assertTrue(all(v is None for v in series))


class TestXauusdAtrExitsAndH1(unittest.TestCase):
    def test_atr_distances_table(self) -> None:
        cases = [
            ("nominal", 4.0, 1.5, 2.0, 1.5, 6.0, 12.0),
            ("floor", 0.5, 1.5, 2.0, 1.5, 1.5, 3.0),
            ("rr25", 2.0, 1.0, 2.5, 1.5, 2.0, 5.0),
        ]
        for name, atr, mult, rr, floor, want_sl, want_tp in cases:
            with self.subTest(name=name):
                sl, tp = _xauusd_exit_distances_from_atr(
                    atr, atr_mult=mult, tp_rr=rr, atr_min=floor
                )
                self.assertAlmostEqual(sl, want_sl, places=5)
                self.assertAlmostEqual(tp, want_tp, places=5)

    def test_h1_bias_table(self) -> None:
        cases = [
            ("buy_ok", "BUY", 2001.0, 2000.0, True),
            ("buy_block", "BUY", 1999.0, 2000.0, False),
            ("sell_ok", "SELL", 1999.0, 2000.0, True),
            ("sell_block", "SELL", 2001.0, 2000.0, False),
            ("bad_side", "NONE", 2001.0, 2000.0, False),
        ]
        for name, side, price, ema, want in cases:
            with self.subTest(name=name):
                self.assertEqual(_xauusd_h1_bias_allows(side, price, ema), want)

    def test_h4_bias_table(self) -> None:
        cases = [
            ("sell_block_up", "SELL", "UP", False),
            ("sell_ok_down", "SELL", "DOWN", True),
            ("sell_ok_flat", "SELL", "FLAT", True),
            ("buy_block_down", "BUY", "DOWN", False),
            ("buy_ok_up", "BUY", "UP", True),
            ("buy_ok_flat", "BUY", "FLAT", True),
            ("bad_side", "NONE", "UP", False),
            ("case_insensitive", "sell", "up", False),
        ]
        for name, side, trend, want in cases:
            with self.subTest(name=name):
                self.assertEqual(_xauusd_h4_bias_allows(side, trend), want)


if __name__ == "__main__":
    unittest.main()
