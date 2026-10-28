from __future__ import annotations

from trading.domain.mae_excursion import (
    MaeBar,
    excursion_after_signal,
    last_closed_close,
    original_decision_side,
    usd_per_price_from_ticket,
)


def _bars(*rows: tuple[float, float, float, float]) -> list[MaeBar]:
    return [MaeBar(time=t, high=h, low=lo, close=c) for t, h, lo, c in rows]


def test_original_decision_side_prefers_vote_not_fill() -> None:
    cases = [
        ({"side_decision": "BUY", "side": "SELL"}, "BUY"),
        ({"side_decision": "SELL", "side": "BUY"}, "SELL"),
        ({"side_decision": None, "side": "BUY"}, "BUY"),
        ({"side_decision": "", "side": "sell"}, "SELL"),
        ({"side_decision": None, "side": "NONE"}, ""),
        ({}, ""),
    ]
    for kwargs, want in cases:
        assert original_decision_side(**kwargs) == want, kwargs


def test_usd_per_price_from_ticket_table() -> None:
    cases = [
        ("tick", {"lot": 2.0, "tick_value": 1.0, "tick_size": 0.5}, 4.0),
        ("kelly_fallback", {"risk_usd": 0.50, "stop_dist": 0.10}, 5.0),
        ("tick_wins", {"lot": 1.0, "tick_value": 2.0, "tick_size": 1.0, "risk_usd": 0.5, "stop_dist": 0.1}, 2.0),
        ("empty", {}, 0.0),
        ("zero_dist", {"risk_usd": 0.5, "stop_dist": 0.0}, 0.0),
    ]
    for name, kwargs, want in cases:
        got = usd_per_price_from_ticket(**kwargs)
        assert abs(got - want) < 1e-9, name


def test_last_closed_close_no_lookahead() -> None:
    bars = _bars(
        (1000.0, 11.0, 9.0, 10.0),
        (1060.0, 12.0, 10.0, 11.0),
        (1120.0, 13.0, 11.0, 12.0),
    )
    assert last_closed_close(bars, 1120.0) == 11.0
    assert last_closed_close(bars, 1119.0) == 10.0
    assert last_closed_close(bars, 1060.0) == 10.0
    assert last_closed_close(bars, 1059.0) is None
    assert last_closed_close(bars, 999.0) is None


def test_excursion_after_signal_table() -> None:
    # signal at t=1060 (bar 1000 already closed). Window 60s includes bar 1060 (closes 1120).
    base = _bars(
        (1000.0, 10.5, 9.5, 10.0),
        (1060.0, 10.8, 9.2, 9.4),
        (1120.0, 11.5, 9.0, 9.1),
    )
    cases = [
        (
            "buy_mae_from_low",
            {"side": "BUY", "signal_ts": 1060.0, "window_seconds": 60.0, "ref": 10.0},
            {"missing": False, "mae": 0.8, "mfe": 0.8, "n_bars": 1, "ambiguous": True},
        ),
        (
            "sell_mae_from_high",
            {"side": "SELL", "signal_ts": 1060.0, "window_seconds": 60.0, "ref": 10.0},
            {"missing": False, "mae": 0.8, "mfe": 0.8, "n_bars": 1, "ambiguous": True},
        ),
        (
            "buy_two_bars_mae_wins",
            {"side": "BUY", "signal_ts": 1060.0, "window_seconds": 120.0, "ref": 10.0},
            {"missing": False, "mae": 1.0, "mfe": 1.5, "n_bars": 2, "mae_gt_mfe": False},
        ),
        (
            "window_excludes_later_bar",
            {"side": "BUY", "signal_ts": 1060.0, "window_seconds": 60.0, "ref": 10.0},
            {"n_bars": 1},
        ),
        (
            "empty_window",
            {"side": "BUY", "signal_ts": 2000.0, "window_seconds": 60.0, "ref": 10.0},
            {"missing": True, "reason": "missing_bars"},
        ),
        (
            "bad_side",
            {"side": "NONE", "signal_ts": 1060.0, "window_seconds": 60.0, "ref": 10.0},
            {"missing": True, "reason": "bad_side"},
        ),
        (
            "usd_scale",
            {
                "side": "SELL",
                "signal_ts": 1060.0,
                "window_seconds": 60.0,
                "ref": 10.0,
                "usd_per_price": 2.0,
            },
            {"mae_usd": 1.6, "mfe_usd": 1.6},
        ),
        (
            "ref_from_last_closed",
            {"side": "BUY", "signal_ts": 1060.0, "window_seconds": 60.0},
            {"ref": 10.0, "missing": False},
        ),
    ]
    for name, kwargs, want in cases:
        got = excursion_after_signal(base, **kwargs)
        for key, val in want.items():
            assert getattr(got, key) == val, (name, key, getattr(got, key), val)


def test_lookahead_bar_after_window_end_is_dropped() -> None:
    bars = _bars(
        (1000.0, 10.2, 9.8, 10.0),
        (1060.0, 10.1, 9.9, 10.0),
        (1120.0, 50.0, 1.0, 10.0),
    )
    got = excursion_after_signal(
        bars, side="BUY", signal_ts=1060.0, window_seconds=60.0, ref=10.0
    )
    assert got.n_bars == 1
    assert got.mae == 0.1
    assert got.mfe == 0.1
    assert got.mae != 9.0
