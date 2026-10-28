from __future__ import annotations

import math

from trading.domain.quant_factors import (
    apply_context_adjustments,
    bollinger_bandwidth,
    directional_probability,
    normal_cdf,
    normal_two_tailed_survival,
    price_zscore,
    relative_volume,
    rsi_state,
    wilder_rsi_series,
)


def test_normal_cdf_and_directional_probability_table():
    cases = [
        ("cdf zero", normal_cdf(0.0), 0.5),
        ("cdf symmetry", normal_cdf(1.0) + normal_cdf(-1.0), 1.0),
        ("survival at zero", normal_two_tailed_survival(0.0), 1.0),
        ("q at zero score", directional_probability(0.0), 0.5),
        ("q positive above half", directional_probability(1.0) > 0.5, True),
        ("q nan is half", directional_probability(float("nan")), 0.5),
        ("q bad temperature", directional_probability(1.0, temperature=0.0), 0.5),
    ]
    for name, got, want in cases:
        if want is True:
            assert got, name
        else:
            assert abs(float(got) - float(want)) < 1e-9, name


def test_price_zscore_table():
    window = [1.0, 2.0, 3.0, 4.0, 5.0]
    wma = (1 + 4 + 9 + 16 + 25) / 15.0
    sd = math.sqrt(2.5)
    cases = [
        ("known window", price_zscore(window, lookback=5), (5.0 - wma) / sd),
        ("too short", price_zscore(window, lookback=10), None),
        ("flat none", price_zscore([3.0] * 8, lookback=5), None),
        ("drops nan", price_zscore([1, float("nan"), 2, 3, 4, 5], lookback=5) is not None, True),
        ("zero lookback", price_zscore(window, lookback=0), None),
    ]
    for name, got, want in cases:
        if want is None:
            assert got is None, name
        elif want is True:
            assert got, name
        else:
            assert abs(float(got) - float(want)) < 1e-9, name


def test_relative_volume_table():
    cases = [
        ("spike", relative_volume([1, 1, 1, 1, 4], lookback=4), 4.0),
        ("average", relative_volume([2, 2, 2], lookback=2), 1.0),
        ("too short", relative_volume([1, 2], lookback=4), None),
        ("zero baseline", relative_volume([0, 0, 0, 5], lookback=3), None),
        ("empty", relative_volume([], lookback=3), None),
    ]
    for name, got, want in cases:
        if want is None:
            assert got is None, name
        else:
            assert abs(float(got) - float(want)) < 1e-12, name


def test_bollinger_bandwidth_known_window_and_squeeze():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    state = bollinger_bandwidth(closes, period=5, rank_lookback=20)
    assert state is not None
    sd = math.sqrt(sum((v - 3.0) ** 2 for v in closes) / 5.0)
    assert abs(state.middle - 3.0) < 1e-12
    assert abs(state.bandwidth - (4.0 * sd) / 3.0) < 1e-9
    assert state.squeeze is None

    volatile = [100.0 + (8.0 if i % 2 == 0 else -8.0) for i in range(80)]
    quiet = [100.0 + (0.01 if i % 2 == 0 else -0.01) for i in range(20)]
    squeezed = bollinger_bandwidth(volatile + quiet, period=20, rank_lookback=40, squeeze_quantile=0.20)
    assert squeezed is not None
    assert squeezed.squeeze is True
    assert squeezed.bandwidth_percentile is not None
    assert squeezed.bandwidth_percentile <= 0.20

    assert bollinger_bandwidth([1.0, 2.0], period=20) is None
    assert bollinger_bandwidth([], period=5) is None


def test_rsi_rising_and_short_series():
    rising = [float(i) for i in range(1, 40)]
    state = rsi_state(rising, atr_value=1.0, period=14, lag=3)
    assert state.rsi is not None and state.rsi > 70.0
    assert state.velocity is not None
    assert state.velocity_atr is not None
    short = rsi_state([1.0, 2.0, 3.0], atr_value=0.1)
    assert short.rsi is None and short.velocity is None
    flat = wilder_rsi_series([5.0] * 30, period=14)
    assert flat[-1] == 50.0


def test_context_adjustments_table():
    cases = [
        ("unchanged", 0.4, dict(rvol=1.2, momentum=0.2, squeeze=False, flow=0.2), 0.4, []),
        ("fakeout damp", 0.8, dict(rvol=0.2, momentum=0.5, squeeze=False, flow=0.2), 0.52, ["fakeout_damp"]),
        ("squeeze boost", 0.5, dict(rvol=1.4, momentum=0.2, squeeze=True, flow=0.4), 0.55, ["squeeze_boost"]),
        ("clip", 0.95, dict(rvol=2.0, momentum=0.1, squeeze=True, flow=0.4), 1.0, ["squeeze_boost"]),
        ("opposite flow no boost", 0.5, dict(rvol=1.4, momentum=0.1, squeeze=True, flow=-0.4), 0.5, []),
        ("missing rvol", 0.3, dict(rvol=None, momentum=0.9, squeeze=None, flow=None), 0.3, []),
        ("nan score", float("nan"), dict(rvol=None, momentum=None, squeeze=None, flow=None), 0.0, []),
    ]
    for name, score, kwargs, want, notes in cases:
        got, meta = apply_context_adjustments(score, **kwargs)
        assert abs(got - want) < 1e-9, name
        assert meta["notes"] == notes, name
        assert -1.0 <= got <= 1.0


def test_volume_delta_table():
    from trading.domain.quant_factors import TICK_FLAG_BUY, TICK_FLAG_SELL, volume_delta

    cases = [
        ("empty", [], [], None),
        ("unsigned", [10.0, 4.0], [0, 0], None),
        ("buy only", [3.0, 1.0], [TICK_FLAG_BUY, TICK_FLAG_BUY], 1.0),
        ("sell only", [2.0], [TICK_FLAG_SELL], -1.0),
        ("balanced", [5.0, 5.0], [TICK_FLAG_BUY, TICK_FLAG_SELL], 0.0),
        ("buy tilt", [3.0, 1.0], [TICK_FLAG_BUY, TICK_FLAG_SELL], 0.5),
        ("both flags ignored", [9.0], [TICK_FLAG_BUY | TICK_FLAG_SELL], None),
        ("zero volume ignored", [0.0, 4.0], [TICK_FLAG_BUY, TICK_FLAG_SELL], -1.0),
    ]
    for name, volumes, flags, want in cases:
        got = volume_delta(volumes, flags)
        if want is None:
            assert got is None, name
        else:
            assert got is not None and abs(got - want) < 1e-12, name
