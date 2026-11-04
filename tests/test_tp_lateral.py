"""TP chico lateral: time window, zone distance, calm market, daily cap and end of the test."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from analyze_tp_chico_lateral import calm as backtest_calm
from trading.domain.rebound_zones import Zone
from trading.domain.tp_lateral import (
    BLOCK_BIG, BLOCK_DAY, BLOCK_DONE, BLOCK_FAR, BLOCK_MONDAY, BLOCK_NIGHT, BLOCK_RUN, LateralState, calm_block,
    entry_block, record_close, time_block, zone_distance,
)

TUE_10 = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
Z = [Zone(low=99.0, high=100.0, kind="techo", touches=3, origin="h1_60d")]


@pytest.mark.parametrize("ts,block", [
    (datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc), BLOCK_MONDAY),
    (datetime(2026, 11, 3, 5, 59, tzinfo=timezone.utc), BLOCK_NIGHT),
    (TUE_10, ""),
])
def test_time_block(ts, block):
    assert time_block(ts) == block


def test_zone_distance():
    assert zone_distance(99.5, Z) == 0.0
    assert zone_distance(98.0, Z) == pytest.approx(1 / 98)
    assert zone_distance(100.0, []) is None


def flat(n=120, last_range=1.0):
    highs = [101.0] * n
    lows = [100.0] * n
    highs[-1] = 100.0 + last_range
    return highs, lows, [100.5] * n


def test_calm_and_big_candle():
    assert calm_block(*flat()) == ""
    assert calm_block(*flat(last_range=3.0)) == BLOCK_BIG
    assert calm_block(*flat(n=50)) == BLOCK_BIG  # not enough history: do not enter


def test_run_of_one_percent_in_four_hours():
    h, l, c = flat()
    c[-1] = 101.6
    assert calm_block(h, l, c) == BLOCK_RUN


def test_calm_matches_backtest():
    import numpy as np

    class M:
        pass
    h, l, c = flat(last_range=2.6)
    m = M()
    m.cl = np.array(c)
    rng = np.array(h) - np.array(l)
    assert (calm_block(h, l, c) == "") == backtest_calm(m, len(c) - 1, rng)


def test_entry_order_and_daily_cap():
    s = LateralState()
    assert entry_block(s, TUE_10, 0.005, "") == ""
    assert entry_block(s, TUE_10, 0.02, "") == BLOCK_FAR
    record_close(s, -10.0, "2026-11-03")
    record_close(s, -10.0, "2026-11-03")
    assert s.day_stops == 1 and entry_block(s, TUE_10, 0.0, "") == BLOCK_DAY
    assert entry_block(s, datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc), 0.0, "") == ""  # new day


def test_end_of_test():
    s = LateralState(trades=99)
    record_close(s, 1.0, "2026-11-03")
    assert s.finished == "100_operaciones" and entry_block(s, TUE_10, 0.0, "") == BLOCK_DONE
    s = LateralState(net_usd=-45.0)
    record_close(s, -10.0, "2026-11-03")
    assert s.finished == "perdida_50_usd"
