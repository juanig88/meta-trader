"""
Unit tests for XAUUSD parallel adjustments: EMA cross edge + SL/TP from fill.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402
from bot_mt5 import (  # noqa: E402
    Config,
    _ema_series,
    _xauusd_breakout_skip_reason,
    _xauusd_compute_sl_tp_prices,
    _xauusd_effective_adx_min,
    _xauusd_effective_tp_rr,
    _xauusd_ema_cross_decision,
    _xauusd_entry_mode,
    _xauusd_exit_distances_from_atr,
    _xauusd_h1_bias_allows,
    _xauusd_h4_bias_allows,
    _xauusd_live_bar_skip_reason,
    _xauusd_should_flatten_now,
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
            ("clamp_rr05", 4.0, 1.5, 0.5, 1.5, 6.0, 9.0),
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


class TestXauusdBreakoutGuards(unittest.TestCase):
    def test_tp_rr_and_adx_floors(self) -> None:
        cases = [
            ("rr_low", Config(xauusd_parallel_tp_rr=0.5), 1.5),
            ("rr_ok", Config(xauusd_parallel_tp_rr=2.0), 2.0),
        ]
        for name, cfg, want in cases:
            with self.subTest(name=name):
                self.assertAlmostEqual(_xauusd_effective_tp_rr(cfg), want, places=5)
        self.assertGreaterEqual(_xauusd_effective_adx_min(Config(xauusd_parallel_adx_min=18)), 22.0)
        self.assertEqual(_xauusd_effective_adx_min(Config(xauusd_parallel_adx_min=25)), 25.0)
        self.assertEqual(_xauusd_entry_mode(Config(xauusd_parallel_entry_mode="scalp")), "scalp")
        self.assertEqual(_xauusd_entry_mode(Config(xauusd_parallel_entry_mode="nope")), "breakout")

    def test_breakout_skip_reason_table(self) -> None:
        cases = [
            ("buy_ok", 25.0, 22.0, "BUY", "UP", "UP", "UP", "BUY", None),
            ("adx_flat", 18.0, 22.0, "BUY", "UP", "UP", "UP", "NONE", "adx_flat"),
            ("no_breakout", 30.0, 22.0, "NONE", "UP", "UP", "UP", "NONE", "no_breakout"),
            ("ema_mismatch", 30.0, 22.0, "BUY", "FLAT", "UP", "UP", "NONE", "ema_mismatch"),
            ("h4_bias", 30.0, 22.0, "BUY", "UP", "DOWN", "UP", "NONE", "h4_bias"),
            ("slope", 30.0, 22.0, "BUY", "UP", "UP", "FLAT", "NONE", "slope"),
            ("data_missing", None, 22.0, "BUY", "UP", "UP", "UP", "NONE", "data_missing"),
        ]
        for name, adx, adx_min, sig, ema, h4, slope, dec, want in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    _xauusd_breakout_skip_reason(
                        adx=adx,
                        adx_min=adx_min,
                        signal_m15=sig,
                        ema_dir=ema,
                        trend_h4=h4,
                        slope=slope,
                        decision=dec,
                    ),
                    want,
                )

    def test_flatten_outside_window_table(self) -> None:
        cfg = Config(
            xauusd_parallel_sessions="10:00-16:00,19:00-21:00",
            xauusd_parallel_flatten_outside_window=True,
            weekend_block_enabled=True,
            weekend_trade_symbols=(),
        )
        cases = [
            ("inside_london_ny", datetime(2026, 8, 17, 14, 0), False),
            ("gap_midday_old", datetime(2026, 8, 17, 12, 5), False),  # 12 is inside 10-16
            ("gap_17", datetime(2026, 8, 17, 17, 0), True),
            ("evening_ok", datetime(2026, 8, 17, 19, 30), False),
            ("late_night", datetime(2026, 8, 17, 22, 0), True),
            ("saturday", datetime(2026, 8, 15, 14, 0), True),
        ]
        for name, when, want in cases:
            with self.subTest(name=name):
                self.assertEqual(_xauusd_should_flatten_now(cfg, when), want)

    def test_flatten_can_be_disabled(self) -> None:
        cfg = Config(
            xauusd_parallel_sessions="10:00-16:00",
            xauusd_parallel_flatten_outside_window=False,
        )
        self.assertFalse(_xauusd_should_flatten_now(cfg, datetime(2026, 8, 17, 17, 0)))


class TestXauusdLiveBarSkipFromMt5Trades(unittest.TestCase):
    """Replay OHLC of magic-260415 fills (broker clock from the candle dump)."""

    def test_live_bar_skip_table(self) -> None:
        cases = [
            # T2 winner: compact M15, close not extreme, fill near EMA50.
            (
                "t2_tp_compact_breakout",
                "BUY",
                4381.18,
                4374.19,
                4380.76,
                7.07,
                4381.61,
                4376.63,
                1.74,
                None,
            ),
            # T5 loser: 1.55×ATR close on the high.
            (
                "t5_sl_exhaustion",
                "BUY",
                4386.92,
                4377.69,
                4385.94,
                5.94,
                4384.39,
                4372.86,
                1.92,
                "anti_chase_exhaustion",
            ),
            # T6 overlapping winner: still exhaustion (same chase as T5).
            (
                "t6_tp_exhaustion",
                "BUY",
                4390.94,
                4378.52,
                4389.97,
                7.04,
                4390.46,
                4373.80,
                1.16,
                "anti_chase_exhaustion",
            ),
            # T7 loser: 2.08×ATR → hard anti_chase (also m1 spike 12.95).
            (
                "t7_sl_hard_range",
                "BUY",
                4405.66,
                4391.96,
                4405.15,
                6.59,
                4404.88,
                4393.24,
                12.95,
                "anti_chase",
            ),
            # T1 loser: 1.62×ATR close near high.
            (
                "t1_sl_exhaustion",
                "BUY",
                4393.01,
                4380.46,
                4391.46,
                7.76,
                4391.10,
                4381.53,
                1.23,
                "anti_chase_exhaustion",
            ),
            # T4 lucky 1-min TP: close not in top 20%, but fill 1.94×ATR above EMA50.
            (
                "t4_tp_extension",
                "BUY",
                4422.60,
                4412.93,
                4419.23,
                6.37,
                4422.34,
                4409.98,
                1.26,
                "chase_extension",
            ),
            # Isolated M1 spike on an otherwise normal M15.
            (
                "m1_spike_only",
                "BUY",
                4400.0,
                4396.0,
                4398.0,
                6.0,
                4398.0,
                4397.0,
                5.0,
                "m1_spike",
            ),
            ("missing_atr", "BUY", 4400.0, 4390.0, 4399.0, 0.0, 4399.0, 4390.0, 1.0, None),
            ("none_side", "NONE", 4400.0, 4390.0, 4399.0, 5.0, 4399.0, 4390.0, 1.0, None),
        ]
        for name, side, hi, lo, cl, atr, px, ema, m1, want in cases:
            with self.subTest(name=name):
                got = _xauusd_live_bar_skip_reason(
                    side=side,
                    bar_high=hi,
                    bar_low=lo,
                    bar_close=cl,
                    atr=atr,
                    price=px,
                    ema=ema,
                    last_m1_range=m1,
                )
                self.assertEqual(got, want)


class TestXauusdParallelPositionsMerge(unittest.TestCase):
    def test_merges_symbol_filter_with_all_magic(self) -> None:
        class Pos:
            def __init__(self, ticket: int, magic: int, symbol: str) -> None:
                self.ticket = ticket
                self.magic = magic
                self.symbol = symbol

        cfg = Config(xauusd_parallel_symbol="XAUUSD", xauusd_parallel_magic=260415)

        def fake_positions_get(symbol=None):
            if symbol == "XAUUSD":
                return [Pos(1862202807, 260415, "XAUUSD")]
            return [
                Pos(1862202807, 260415, "XAUUSD"),
                Pos(1862366673, 260415, "XAUUSD"),
                Pos(99, 260414, "EURUSD"),
            ]

        with patch.object(bot_mt5.mt5, "positions_get", side_effect=fake_positions_get):
            found = bot_mt5._xauusd_parallel_positions(cfg)
        tickets = sorted(int(p.ticket) for p in found)
        self.assertEqual(tickets, [1862202807, 1862366673])

    def test_empty_when_magic_zero(self) -> None:
        cfg = Config(xauusd_parallel_magic=0)
        self.assertEqual(bot_mt5._xauusd_parallel_positions(cfg), [])


if __name__ == "__main__":
    unittest.main()
