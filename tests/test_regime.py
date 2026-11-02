"""Regime: structure trend, 3-of-4 vote, operating zones, stop beyond the zone, pause and resume."""

from __future__ import annotations

from datetime import datetime

from trading.domain.impulso_m1 import BUY, SELL, Bar
from trading.domain.rebound_zones import CEILING, FLOOR, Zone
from trading.domain.regime import (
    DOWN, RANGE, UP, Regime, allowed_sides, held_trend, initial_trend, market_trend, operating_zones, return_target,
    risk_cap, status_key,
    status_text, structure_trend,
    zone_stop,
)

T = datetime(2026, 10, 31)


def zigzag(*pivots: float, step: int = 4) -> list[Bar]:
    """Linear legs between pivots, ``step`` bars per leg, 10-wide candles."""
    out = []
    for a, b in zip(pivots, pivots[1:]):
        for k in range(step):
            c = a + (b - a) * k / step
            out.append(Bar(c, c + 5, c - 5, c))
    c = pivots[-1]
    out += [Bar(c, c + 5, c - 5, c)] * 4
    return out


def test_structure_up_down_and_mixed():
    assert structure_trend(zigzag(100, 120, 110, 130, 120, 140)) == 1
    assert structure_trend(zigzag(140, 120, 130, 110, 120, 100)) == -1
    assert structure_trend(zigzag(100, 140, 110, 130, 105, 135)) == 0


def test_three_of_four_and_sides():
    assert market_trend([1, 1, 1, -1]) == UP
    assert market_trend([-1, -1, 0, -1]) == DOWN
    assert market_trend([1, 1, 0, -1]) == RANGE
    assert allowed_sides(UP) == (BUY,) and allowed_sides(DOWN) == (SELL,) and allowed_sides(RANGE) == ()


def test_operating_zones_and_stops():
    zs = [Zone(75500, 76500, "manual", origin="manual"), Zone(77738, 78660, CEILING), Zone(74891, 74980, FLOOR)]
    floor, ceiling = operating_zones(zs, 77100)
    assert floor.low == 75500 and ceiling.high == 78660
    assert zone_stop(BUY, floor, ceiling) == 75500 * 0.999
    assert zone_stop(SELL, floor, ceiling) == 78660 * 1.001


def test_break_pauses_and_only_a_new_zone_resumes():
    zs = [Zone(76000, 76500, FLOOR), Zone(78000, 78500, CEILING)]
    r = Regime()
    assert r.on_h1_close(T, 77000, zs) is None and r.floor.low == 76000
    assert r.on_h1_close(T, 75800, zs).startswith("ruptura_piso") and r.paused
    assert r.on_h1_close(T, 77000, zs) is None and r.paused  # coming back does not resume by itself
    flat = [Bar(75300, 75305, 75295, 75300)] * 10
    r2 = Regime(paused_since=T.isoformat())
    assert r2.try_resume(flat, 75300) == [] and r2.paused


def test_two_touches_with_bounce_resume():
    r = Regime(paused_since=T.isoformat(), reason="sl_contra_zona")
    # two lows near 75000, each followed by a >1% bounce, 4+ bars apart
    bars = zigzag(75600, 75000, 75900, 75020, 75900, step=5)
    found = r.try_resume(bars, 75900)
    assert found and not r.paused and r.floor is not None and abs(r.floor.low - 75000) < 30


def test_state_roundtrip():
    r = Regime(paused_since=T.isoformat(), reason="x", floor=Zone(1, 2, FLOOR))
    assert Regime.from_dict(r.as_dict()).as_dict() == r.as_dict()


def test_status_text_names_floor_ceiling_trend_and_state():
    r = Regime(floor=Zone(76321, 77103, FLOOR, origin="m30_48h_reciente"), ceiling=Zone(77738, 78660, CEILING))
    txt = status_text(77300, RANGE, [0, 0, 1, -1], r)
    assert "RANGO" in txt and "nada hasta que haya tendencia" in txt and "W1 sube" in txt and "MN1 baja" in txt
    assert "Techo: 77,738–78,660" in txt and "Piso: 76,321–77,103" in txt and "Operando" in txt
    assert "solo ventas (SELL)" in status_text(77300, DOWN, [0, -1, 0, -1], r, since="2026-10-12T05:00")
    r.pause(T, "ruptura_piso 76321-77103")
    assert "EN PAUSA" in status_text(76000, RANGE, [0, 0, 1, -1], r)
    assert status_key(RANGE, r) != status_key(UP, r)


def test_trend_is_held_through_bounces_until_three_turn():
    assert held_trend(DOWN, RANGE) == DOWN      # a bounce: H4/D1 turn up, still bearish
    assert held_trend(DOWN, UP) == UP           # 3 of 4 up: the change is real
    assert initial_trend([[-1, -1, -1, 0], [1, 0, 0, -1], [0, 1, 1, -1]]) == DOWN
    assert initial_trend([[0, 0, 1, -1]]) == RANGE


def test_break_is_only_a_suspicion_then_false_break_returns():
    zs = [Zone(78000, 78500, CEILING), Zone(76000, 76500, FLOOR)]
    r = Regime()
    assert r.on_h1_bar(T, 78400, 77900, 78300, 78200, zs) is None
    ev = r.on_h1_bar(T, 78900, 78300, 78800, 78300, zs)  # closes over the ceiling: suspicion only
    assert ev[0] == "sospecha" and ev[1] == 1 and r.watch and not r.paused
    assert r.on_h1_bar(T, 79100, 78700, 78900, 78800, zs) is None and r.watch["extreme"] == 79100
    ev = r.on_h1_bar(T, 78950, 78200, 78400, 78900, zs)  # back inside: false break, sell the return
    assert ev[0] == "falsa" and -ev[1] == SELL and ev[2] == 79100 and r.watch is None


def test_break_confirms_only_far_and_long_enough():
    zs = [Zone(76000, 76500, FLOOR)]
    r = Regime()
    assert r.on_h1_bar(T, 76200, 75800, 75900, 76100, zs)[0] == "sospecha"
    for _ in range(30):  # a day outside but only ~1% away: still evaluating
        assert r.on_h1_bar(T, 75300, 75100, 75200, 75200, zs) is None
    assert r.watch and not r.paused
    ev = r.on_h1_bar(T, 74400, 74300, 74400, 75200, zs)  # >2% under the floor after 24h+
    assert ev[0] == "confirmada" and r.paused and r.watch is None
    assert Regime.from_dict(r.as_dict()).as_dict() == r.as_dict()


def test_risk_cap_and_return_target():
    assert risk_cap(-0.05) == 4.0 and risk_cap(8) == 4.0 and risk_cap(19) == 9.5 and risk_cap(30) == 15.0
    zs = [Zone(76000, 76500, FLOOR), Zone(74000, 74400, FLOOR), Zone(79000, 79500, CEILING)]
    assert return_target(SELL, 78000, zs) == 76500
    assert return_target(BUY, 77000, zs) == 79000
    assert return_target(SELL, 73000, zs) is None


def test_status_text_shows_evaluation():
    r = Regime(watch={"low": 76000, "high": 76500, "dir": -1, "origin": "x", "since": "2026-10-31T05:00:00",
                      "extreme": 75600, "hours": 3})
    txt = status_text(75900, DOWN, [0, -1, 0, -1], r)
    assert "EVALUANDO posible ruptura del piso 76,000–76,500" in txt and "No abre nada" in txt


def test_status_text_shows_break_volume():
    r = Regime(watch={"low": 76000, "high": 76500, "dir": -1, "origin": "x", "since": "2026-10-31T05:00:00",
                      "extreme": 75600, "hours": 1, "vol_rel": 0.6})
    assert "0.60x lo normal (bajo)" in status_text(75900, DOWN, [0, -1, 0, -1], r)
