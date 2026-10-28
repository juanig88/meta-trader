"""Clamp rotation SL/TP so cheap CFDs keep a stop near the quote, not at zero."""

from __future__ import annotations

from trading.domain.rotation_stops import (
    clamp_symmetric_stops,
    is_floor_parked_sl,
    rotation_spread_skip_reason,
    rotation_stop_distance,
    size_lot_for_percent_stop,
    spread_usd_for_lot,
    stop_off_percent,
    stop_distance_too_wide,
    volatility_stop_distance,
)


def test_clamp_symmetric_stops_table():
    cases = [
        (
            "snx money-stop through zero",
            dict(side="BUY", entry=0.2562, sl=-33.06, tp=33.57, min_dist=0.0001, point=0.0001, digits=5),
            True,
        ),
        (
            "dyd similar",
            dict(side="BUY", entry=0.1410, sl=-33.18, tp=33.46, min_dist=0.00001, point=0.00001, digits=5),
            True,
        ),
        (
            "gal screenshot floor",
            dict(side="BUY", entry=0.00264, sl=0.00002, tp=0.00526, min_dist=0.00001, point=0.00001, digits=5),
            True,
        ),
        (
            "already valid buy inside 5pct",
            dict(side="BUY", entry=100.0, sl=99.5, tp=100.5, min_dist=0.01, point=0.01, digits=2),
            True,
        ),
        (
            "price too small for min stop",
            dict(side="BUY", entry=0.0002, sl=-1.0, tp=1.0, min_dist=0.001, point=0.0001, digits=5),
            False,
        ),
        (
            "sell stays above entry",
            dict(side="SELL", entry=0.2562, sl=33.0, tp=-32.0, min_dist=0.0001, point=0.0001, digits=5),
            True,
        ),
    ]
    for name, kwargs, want_ok in cases:
        got = clamp_symmetric_stops(**kwargs)
        if not want_ok:
            assert got is None, name
            continue
        assert got is not None, name
        sl, tp = got
        px = float(kwargs["entry"])
        side = kwargs["side"]
        if side == "BUY":
            assert 0.0 < sl < px, name
            assert sl >= px * 0.94, name
            assert tp > px, name
            assert abs((px - sl) - (tp - px)) <= 2 * (10 ** (-int(kwargs["digits"]))), name
        else:
            assert sl > px, name
            assert sl <= px * 1.06, name
            assert 0.0 < tp < px, name
            assert abs((sl - px) - (px - tp)) <= 2 * (10 ** (-int(kwargs["digits"]))), name


def test_volatility_stop_distance_table():
    # 1.5×ATR(M5) when that is the wider scale.
    d = volatility_stop_distance(entry=1.0, atr_m5=0.01, atr_m1=0.002, min_dist=0.0)
    assert d is not None and abs(d - 0.015) < 1e-9
    # 2×ATR(M1) wins if M1 noise is larger.
    d = volatility_stop_distance(entry=1.0, atr_m5=0.002, atr_m1=0.01, min_dist=0.0)
    assert d is not None and abs(d - 0.02) < 1e-9
    # Huge ATR still caps at 5% of price.
    d = volatility_stop_distance(entry=1.0, atr_m5=1.0, atr_m1=1.0, min_dist=0.0)
    assert d is not None and abs(d - 0.05) < 1e-9
    # No ATR → 5% fallback.
    d = volatility_stop_distance(entry=2.0, atr_m5=None, atr_m1=None, min_dist=0.0)
    assert d is not None and abs(d - 0.10) < 1e-9
    # Spread floor.
    d = volatility_stop_distance(entry=1.0, atr_m5=0.001, atr_m1=None, min_dist=0.02)
    assert d is not None and abs(d - 0.02) < 1e-9
    assert volatility_stop_distance(entry=0.01, atr_m5=0.001, min_dist=0.02) is None


def test_size_lot_for_percent_stop_table():
    rsr = size_lot_for_percent_stop(
        risk_usd=0.33,
        price=0.00167,
        tick_value=1.0,
        tick_size=1.0,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100000.0,
        max_risk_usd=0.50,
    )
    assert rsr.ok is True
    assert rsr.lot > 100.0
    assert abs(rsr.dist / 0.00167 - 0.05) < 1e-9
    assert abs(rsr.usd - 0.33) < 0.02
    btc = size_lot_for_percent_stop(
        risk_usd=0.33,
        price=100000.0,
        tick_value=1.0,
        tick_size=1.0,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
        max_risk_usd=0.50,
    )
    assert btc.ok is True
    assert abs(btc.lot - 0.01) < 1e-9
    assert btc.dist < 100000.0 * 0.05
    assert btc.usd <= 0.50 + 1e-9


def test_rotation_spread_skip_reason_table():
    cases = [
        (
            "btc spread covers money stop",
            dict(
                spread_px=80.0,
                stop_dist=50.0,
                lot=0.01,
                tick_value=1.0,
                tick_size=1.0,
                risk_usd=0.50,
            ),
            "spread_cubre_stop",
        ),
        (
            "eth-like hole 65pct of risk",
            dict(
                spread_px=32.5,
                stop_dist=50.0,
                lot=0.01,
                tick_value=1.0,
                tick_size=1.0,
                risk_usd=0.50,
            ),
            "spread_caro",
        ),
        (
            "cheap alt ~5pct of risk",
            dict(
                spread_px=0.00025,
                stop_dist=0.05,
                lot=100.0,
                tick_value=1.0,
                tick_size=1.0,
                risk_usd=0.50,
            ),
            "",
        ),
        (
            "missing stop",
            dict(
                spread_px=0.01,
                stop_dist=0.0,
                lot=0.01,
                tick_value=1.0,
                tick_size=1.0,
                risk_usd=0.50,
            ),
            "spread_sin_stop",
        ),
    ]
    for name, kwargs, want in cases:
        got = rotation_spread_skip_reason(**kwargs)
        assert got == want, name
    hole = spread_usd_for_lot(spread_px=32.5, lot=0.01, tick_value=1.0, tick_size=1.0)
    assert hole is not None and abs(hole - 0.325) < 1e-9


def test_rotation_stop_distance_money_vs_percent():
    # BTC: $0.50 at min lot is << 5% → money stop.
    btc = rotation_stop_distance(
        price=100000.0,
        lot=0.01,
        tick_value=1.0,
        tick_size=1.0,
        max_risk_usd=0.50,
    )
    assert btc is not None
    assert abs(btc - 50.0) < 1e-9
    assert btc < 100000.0 * 0.05
    # Cheap CFD: $0.50 at the sized lot would be >5% → keep 5%.
    cheap = rotation_stop_distance(
        price=0.00167,
        lot=3952.0,
        tick_value=1.0,
        tick_size=1.0,
        max_risk_usd=0.50,
    )
    assert cheap is not None
    assert abs(cheap / 0.00167 - 0.05) < 1e-6
    assert rotation_stop_distance(
        price=1.0, lot=0.01, tick_value=1.0, tick_size=1.0, max_risk_usd=0.50, min_dist=0.2
    ) == 0.2


def test_stop_off_percent():
    # RSR screenshot: 20-point SL is not 5%.
    assert stop_off_percent(side="BUY", entry=0.001666, sl=0.001651) is True
    # Already ±5%.
    assert stop_off_percent(side="BUY", entry=1.0, sl=0.95) is False
    assert stop_off_percent(side="BUY", entry=0.00264, sl=0.00002) is True


def test_stop_distance_too_wide():
    # 5% stop vs 1% ATR band → tighten.
    assert stop_distance_too_wide(side="BUY", entry=1.0, sl=0.95, vol_dist=0.01) is True
    # Already on the ATR band.
    assert stop_distance_too_wide(side="BUY", entry=1.0, sl=0.99, vol_dist=0.01) is False
    assert stop_distance_too_wide(side="BUY", entry=0.00264, sl=0.00002, vol_dist=0.00004) is True


def test_is_floor_parked_sl_table():
    assert is_floor_parked_sl(side="BUY", entry=0.00264, sl=0.00002) is True
    assert is_floor_parked_sl(side="BUY", entry=0.24493, sl=0.00002) is True
    assert is_floor_parked_sl(side="BUY", entry=67.64, sl=34.33) is False
    assert is_floor_parked_sl(side="BUY", entry=0.2562, sl=0.2434) is False
    assert is_floor_parked_sl(side="SELL", entry=0.2562, sl=0.5120) is True
    assert is_floor_parked_sl(side="SELL", entry=0.2562, sl=0.2690) is False
