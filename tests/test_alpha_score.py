from __future__ import annotations

import math

from trading.domain.alpha_score import (
    DIRECTION_BUY,
    DIRECTION_NONE,
    DIRECTION_SELL,
    AlphaWeights,
    CostModel,
    combine,
    flow_factor,
    momentum_factor,
    score_bars,
    squash,
    stdev,
    tick_imbalance,
    volatility_factor,
    weighted_moving_average,
    zscore_factor,
)
from trading.domain.expected_value import kelly_fraction, quarter_kelly_risk_pct


def _trend(n: int = 80, start: float = 100.0, step: float = 0.25):
    opens, highs, lows, closes, vols = [], [], [], [], []
    price = start
    for i in range(n):
        o = price
        c = price + step
        opens.append(o)
        highs.append(max(o, c) + 0.08)
        lows.append(min(o, c) - 0.04)
        closes.append(c)
        vols.append(1_000.0 + i * 20.0)
        price = c
    return opens, highs, lows, closes, vols


def _flat(n: int = 80, price: float = 100.0):
    return (
        [price] * n,
        [price] * n,
        [price] * n,
        [price] * n,
        [1_000.0] * n,
    )


def test_squash_and_wma_table():
    cases = [
        ("zero", 0.0, 1.0, 0.0),
        ("scale", 1.0, 1.0, math.tanh(1.0)),
        ("nonpositive scale", 2.0, 0.0, 0.0),
    ]
    for name, value, scale, want in cases:
        assert abs(squash(value, scale) - want) < 1e-12, name

    wma_cases = [
        ("too short", [1.0, 2.0], 3, None),
        ("zero period", [1.0, 2.0, 3.0], 0, None),
        ("linear 1,2,3", [1.0, 2.0, 3.0], 3, (1 * 1 + 2 * 2 + 3 * 3) / 6.0),
    ]
    for name, values, period, want in wma_cases:
        got = weighted_moving_average(values, period)
        if want is None:
            assert got is None, name
        else:
            assert abs(got - want) < 1e-12, name


def test_stdev_edge_cases():
    cases = [
        ("empty", [], None),
        ("one value", [1.0], None),
        ("drops nan", [1.0, float("nan"), 3.0], True),
        ("zero vol", [5.0, 5.0, 5.0], 0.0),
    ]
    for name, values, want in cases:
        got = stdev(values)
        if want is None:
            assert got is None, name
        elif want is True:
            assert got is not None and got > 0.0, name
        else:
            assert abs(got - want) < 1e-12, name


def test_factors_table():
    o, h, low, c, v = _trend()
    fo, fh, fl, fc, fv = _flat()
    cases = [
        ("momentum uptrend positive", momentum_factor(c, 1.0), True),
        ("momentum empty", momentum_factor([], 1.0), None),
        ("momentum zero atr", momentum_factor(c, 0.0), None),
        ("vol expansion defined", volatility_factor(h, low, c) is not None, True),
        ("vol flat none", volatility_factor(fh, fl, fc), None),
        ("zscore anti-chase negative on stretch", zscore_factor(c, lookback=50), False),
        ("zscore flat none", zscore_factor(fc, lookback=50), None),
        ("zscore short none", zscore_factor(c[:10], lookback=50), None),
        ("flow uptrend positive", flow_factor(o, c, v, lookback=20), True),
        ("flow empty", flow_factor([], [], [], lookback=20), None),
    ]
    for name, got, want in cases:
        if want is None:
            assert got is None, name
        elif want is True:
            assert got is not None and got > 0.0, name
        else:
            assert got is not None and got < 0.0, name


def test_tick_imbalance_table():
    cases = [
        ("too few", [1.0] * 5, [1.1] * 5, None),
        ("all up", list(range(20, 40)), [x + 0.1 for x in range(20, 40)], 1.0),
        ("all down", list(range(40, 20, -1)), [x + 0.1 for x in range(40, 20, -1)], -1.0),
        ("flat", [2.0] * 25, [2.1] * 25, 0.0),
        ("nan ticks dropped", [1.0, float("nan"), 1.2] + list(range(10, 30)), [1.1, 1.1, 1.3] + [x + 0.1 for x in range(10, 30)], True),
    ]
    for name, bids, asks, want in cases:
        got = tick_imbalance(bids, asks, min_ticks=20)
        if want is None:
            assert got is None, name
        elif want is True:
            assert got is not None, name
        else:
            assert abs(got - want) < 1e-12, name


def test_combine_renormalises_missing_factors():
    weights = AlphaWeights(momentum=0.5, volatility=0.5, zscore=0.0, flow=0.0)
    score, applied = combine({"momentum": 1.0, "volatility": None}, weights)
    assert abs(score - 1.0) < 1e-12
    assert applied == {"momentum": 1.0}
    empty, applied_empty = combine({}, weights)
    assert empty == 0.0
    assert applied_empty == {}


def test_score_bars_table():
    o, h, low, c, v = _trend()
    fo, fh, fl, fc, fv = _flat()
    happy = score_bars(
        symbol="EURUSD",
        timeframe="M15",
        opens=o,
        highs=h,
        lows=low,
        closes=c,
        volumes=v,
        point=0.00001,
        costs=CostModel(),
        min_score=0.05,
    )
    flat = score_bars(
        symbol="EURUSD",
        timeframe="M15",
        opens=fo,
        highs=fh,
        lows=fl,
        closes=fc,
        volumes=fv,
        point=0.00001,
    )
    empty = score_bars(
        symbol="EURUSD",
        timeframe="M15",
        opens=[],
        highs=[],
        lows=[],
        closes=[],
        point=0.00001,
    )
    nan_closes = [float("nan") if i % 7 == 0 else x for i, x in enumerate(c)]
    dirty = score_bars(
        symbol="EURUSD",
        timeframe="M15",
        opens=o,
        highs=h,
        lows=low,
        closes=nan_closes,
        volumes=v,
        point=0.00001,
        min_score=0.05,
    )
    expensive = score_bars(
        symbol="EURUSD",
        timeframe="M15",
        opens=o,
        highs=h,
        lows=low,
        closes=c,
        volumes=v,
        point=0.00001,
        costs=CostModel(spread_points=1e9, commission_points=0, slippage_points=0),
        min_score=0.05,
    )

    cases = [
        ("uptrend lean buy or none", happy.direction in (DIRECTION_BUY, DIRECTION_NONE), True),
        ("uptrend score finite", math.isfinite(happy.score), True),
        ("flat no direction", flat.direction, DIRECTION_NONE),
        ("empty no direction", empty.direction, DIRECTION_NONE),
        ("empty score zero", empty.score, 0.0),
        ("nan ticks still finite", math.isfinite(dirty.score), True),
        ("costs can wipe direction", expensive.direction, DIRECTION_NONE),
        ("costs make net_edge negative", expensive.net_edge < 0.0, True),
        ("q is a probability", 0.0 < happy.q < 1.0, True),
        ("q precedes kelly", happy.features.get("kelly_applied"), False),
        ("rvol present on a trend", happy.features.get("rvol") is not None, True),
        ("empty q is one half", empty.q, 0.5),
    ]
    for name, got, want in cases:
        if want is True:
            assert got, name
        else:
            assert got == want, name


def test_downtrend_can_score_sell():
    o, h, low, c, v = _trend(step=-0.25)
    score = score_bars(
        symbol="USDJPY",
        timeframe="M15",
        opens=o,
        highs=h,
        lows=low,
        closes=c,
        volumes=v,
        point=0.001,
        costs=CostModel(),
        min_score=0.05,
    )
    assert score.direction in (DIRECTION_SELL, DIRECTION_NONE)
    assert math.isfinite(score.net_edge)


def test_zero_loss_kelly_edge_cases():
    cases = [
        ("no odds", 0.9, 0.0, 0.0),
        ("negative odds", 0.9, -1.0, 0.0),
        ("huge odds almost all-in", 0.9, 1e9, 0.9 - 0.1 / 1e9),
        ("zero win prob", 0.0, 2.0, 0.0),
    ]
    for name, p, b, want in cases:
        assert abs(kelly_fraction(p, b) - want) < 1e-12, name
    assert abs(quarter_kelly_risk_pct(0.8, 2.0, 0.0, fraction=0.25) - 20.0) < 1e-6
    assert quarter_kelly_risk_pct(0.8, 0.0, 0.0, fraction=0.25) == 0.0
