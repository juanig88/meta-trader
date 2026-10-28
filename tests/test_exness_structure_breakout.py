from __future__ import annotations

import pytest

from analyze_exness_structure_breakout import (
    TIMEFRAME_SECONDS,
    _aggregate_bars,
    build_trades,
    ema,
    signal_side,
)


def test_ema_matches_recursive_definition():
    assert ema([10, 12, 14], 2) == pytest.approx([10, 11.3333333333, 13.1111111111])


def test_breakout_signal_uses_only_preceding_range_and_volume():
    times = [i * TIMEFRAME_SECONDS for i in range(30)]
    highs = [100.0] * 30
    lows = [90.0] * 30
    closes = [95.0] * 30
    volumes = [100.0] * 30
    averages = [80.0 + 0.5 * i for i in range(30)]
    highs[29] = 120.0
    closes[29] = 101.0
    volumes[29] = 120.0
    assert signal_side(29, times, highs, lows, closes, volumes, averages) == "BUY"


def test_missing_bar_blocks_signal():
    times = [i * TIMEFRAME_SECONDS for i in range(30)]
    times[20] += 60
    highs = [100.0] * 30
    lows = [90.0] * 30
    closes = [95.0] * 30
    volumes = [100.0] * 30
    averages = [80.0 + 0.5 * i for i in range(30)]
    highs[29] = 120.0
    closes[29] = 101.0
    volumes[29] = 120.0
    assert signal_side(29, times, highs, lows, closes, volumes, averages) is None


def test_short_net_includes_spread_and_fills_next_bar():
    times = [i * TIMEFRAME_SECONDS for i in range(40)]
    highs = [101.0] * 40
    lows = [99.0] * 40
    closes = [100.0] * 40
    volumes = [100.0] * 40
    spreads = [10.0] * 40
    closes[29] = 98.0
    lows[29] = 97.0
    volumes[29] = 120.0
    averages = [102.0 - i * 0.1 for i in range(40)]
    m5 = {
        "time": times,
        "open": [100.0] * 40,
        "high": highs,
        "low": lows,
        "close": closes,
        "spread": spreads,
        "volume": volumes,
    }
    h4 = {"time": [0, 14400, 28800, 43200, 57600], "close": [100.0] * 5, "ema20": [101.0] * 5}
    signals, trades = build_trades(
        m5, h4, point=0.01, contract_size=1.0, volume_min=0.01, horizon_minutes=15
    )
    assert signals[0]["side"] == "SELL"
    assert trades[0]["entry_time"] == times[30]
    assert trades[0]["net_usd_min_lot"] < (trades[0]["entry_price"] - trades[0]["exit_bid"]) * 0.01


def test_h4_aggregation_rejects_partial_buckets():
    rows = [
        {"time": 0, "open": 1, "high": 2, "low": 0, "close": 1, "tick_volume": 1},
        {"time": TIMEFRAME_SECONDS, "open": 1, "high": 2, "low": 0, "close": 1, "tick_volume": 1},
    ]
    assert _aggregate_bars(rows, 4 * 60 * 60) == []
