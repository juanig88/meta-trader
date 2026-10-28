from __future__ import annotations

from trading.domain.fill_mtm import mtm_at_horizon, signed_pnl_usd
from trading.domain.mae_excursion import MaeBar


def _bars(*rows: tuple[float, float, float, float]) -> list[MaeBar]:
    return [MaeBar(time=t, high=h, low=lo, close=c) for t, h, lo, c in rows]


def test_signed_pnl_usd_table() -> None:
    cases = [
        ("buy_up", dict(side="BUY", entry=10.0, price=11.0, usd_per_price=2.0), 2.0),
        ("sell_down", dict(side="SELL", entry=10.0, price=9.0, usd_per_price=2.0), 2.0),
        ("buy_down", dict(side="BUY", entry=10.0, price=9.5, usd_per_price=1.0), -0.5),
        ("bad_side", dict(side="NONE", entry=10.0, price=11.0, usd_per_price=1.0), None),
        ("zero_usd", dict(side="BUY", entry=10.0, price=11.0, usd_per_price=0.0), None),
        ("zero_entry", dict(side="BUY", entry=0.0, price=11.0, usd_per_price=1.0), None),
    ]
    for name, kwargs, want in cases:
        got = signed_pnl_usd(**kwargs)
        if want is None:
            assert got is None, name
        else:
            assert got is not None and abs(got - want) < 1e-9, name


def test_mtm_at_horizon_table() -> None:
    fill_ts = 1060.0
    bars = _bars(
        (1000.0, 10.2, 9.8, 10.0),
        (1060.0, 10.4, 9.6, 9.7),
        (1120.0, 10.6, 9.5, 9.4),
        (1180.0, 10.8, 9.3, 10.5),
    )
    cases = [
        (
            "buy_60s",
            dict(side="BUY", entry=10.0, fill_ts=fill_ts, horizon_seconds=60.0, usd_per_price=1.0),
            {"missing": False, "n_bars": 1, "mark": 9.7, "pnl_usd": -0.3},
        ),
        (
            "sell_60s_is_opposite",
            dict(side="SELL", entry=10.0, fill_ts=fill_ts, horizon_seconds=60.0, usd_per_price=1.0),
            {"missing": False, "n_bars": 1, "mark": 9.7, "pnl_usd": 0.3},
        ),
        (
            "buy_180s",
            dict(side="BUY", entry=10.0, fill_ts=fill_ts, horizon_seconds=180.0, usd_per_price=2.0),
            {"missing": False, "n_bars": 3, "mark": 10.5, "pnl_usd": 1.0},
        ),
        (
            "no_lookahead_past_horizon",
            dict(side="BUY", entry=10.0, fill_ts=fill_ts, horizon_seconds=60.0, usd_per_price=1.0),
            {"missing": False, "n_bars": 1, "mark": 9.7},
        ),
        (
            "empty_window",
            dict(side="BUY", entry=10.0, fill_ts=2000.0, horizon_seconds=60.0, usd_per_price=1.0),
            {"missing": True, "reason": "missing_bars"},
        ),
        (
            "bad_side",
            dict(side="HOLD", entry=10.0, fill_ts=fill_ts, horizon_seconds=60.0, usd_per_price=1.0),
            {"missing": True, "reason": "bad_side"},
        ),
        (
            "missing_usd",
            dict(side="BUY", entry=10.0, fill_ts=fill_ts, horizon_seconds=60.0, usd_per_price=0.0),
            {"missing": True, "reason": "missing_usd"},
        ),
    ]
    for name, kwargs, want in cases:
        got = mtm_at_horizon(bars, **kwargs)
        for key, val in want.items():
            assert getattr(got, key) == val, f"{name}.{key}: {getattr(got, key)!r} != {val!r}"
