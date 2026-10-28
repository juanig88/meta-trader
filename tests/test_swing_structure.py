"""Murphy swing snapshot: no lookahead; ensemble skips climax."""

from __future__ import annotations

from trading.domain.swing_structure import confirmed_pivots, murphy_snapshot, swing_context


def _path(prices: list[float], *, pad: float = 0.05) -> tuple[list[float], list[float], list[float]]:
    highs = [p + pad for p in prices]
    lows = [p - pad for p in prices]
    return highs, lows, list(prices)


def test_confirmed_pivots_need_right_wing():
    px = [1, 2, 3, 4, 10, 4, 3]
    h, l, _c = _path(px, pad=0.01)
    piv = confirmed_pivots(h, l, wing=2)
    highs = [p for p in piv if p[2] == "high"]
    assert highs
    assert highs[-1][0] <= len(px) - 1 - 2


def test_swing_context_table():
    pull = 20.0 - 0.45 * (20.0 - 8.0)
    climax = 19.4
    deep = 20.0 - 0.70 * (20.0 - 8.0)
    base = [10.0, 10.2, 9.8, 9.5, 8.0, 8.4, 9.0, 11.0, 14.0, 16.0, 18.0, 19.0, 20.0]
    cases = [
        (
            "pullback 45%",
            base + [18.0, 16.5, pull, pull, pull],
            "BUY",
            "pullback_38_50",
            False,
            True,
        ),
        (
            "climax under unbroken high",
            base + [19.6, climax, climax, climax],
            "BUY",
            "climax_unbroken",
            True,
            False,
        ),
        (
            "deep retrace",
            base + [16.0, 14.0, deep, deep, deep],
            "BUY",
            "deep_retrace",
            False,
            False,
        ),
        (
            "too short",
            [1.0, 2.0, 3.0],
            "BUY",
            None,
            False,
            False,
        ),
    ]
    for name, prices, side, tag, veto, keep in cases:
        h, l, c = _path(prices, pad=0.02)
        got = swing_context(h, l, c, side=side, tf="M15")
        if tag is None:
            assert got["ok"] is False, name
            continue
        assert got["ok"] is True, name
        assert got["tag"] == tag, (
            f"{name}: tag {got['tag']!r} retrace={got.get('retrace')} "
            f"last_h={got.get('last_high')} close={got.get('close')} pivots={got.get('n_pivots')}"
        )
        assert got["would_veto"] is veto, name
        assert got["would_keep"] is keep, name


def test_retest_after_break():
    # Peak at 20, two lower bars to confirm it, then a 1%/ATR break, then retest.
    base = [10.0, 10.2, 9.8, 9.5, 8.0, 8.4, 9.0, 11.0, 14.0, 16.0, 18.0, 19.0, 20.0]
    prices = base + [18.5, 17.8, 18.2, 22.0, 21.0, 20.1, 20.05, 20.05]
    h, l, c = _path(prices, pad=0.02)
    h[len(base) + 3] = 23.0
    got = swing_context(h, l, c, side="BUY", tf="M15")
    assert got["ok"] is True
    assert got["tag"] == "retest_broken", got
    assert got["broken_level"] is not None
    assert abs(float(got["broken_level"]) - 20.02) < 0.05
    assert got["would_keep"] is True
    assert got["would_veto"] is False


def test_murphy_snapshot_nests_h4():
    prices = [
        10.0, 10.2, 9.8, 9.5, 8.0, 8.4, 9.0, 11.0, 14.0, 16.0, 18.0, 19.0, 20.0,
        18.0, 16.5, 14.6, 14.6, 14.6,
    ]
    h, l, c = _path(prices, pad=0.02)
    got = murphy_snapshot(
        side="BUY",
        m15_highs=h,
        m15_lows=l,
        m15_closes=c,
        h4_highs=h,
        h4_lows=l,
        h4_closes=c,
    )
    assert got["tag_m15"] == "pullback_38_50"
    assert got["would_keep"] is True
    empty = murphy_snapshot(side="BUY", m15_highs=h, m15_lows=l, m15_closes=c)
    assert empty["h4"]["ok"] is False
