"""Rebound zones: H1 swing clustering, manual zones, and the floor/ceiling entry block."""

from __future__ import annotations

from trading.domain.impulso_m1 import BUY, SELL, Bar
from trading.domain.rebound_zones import (
    BOTH, CEILING, FLOOR, Zone, find_recent_zones, find_zones, parse_manual_zones, zone_block,
)


def v_bottom(low: float, n: int = 7, top: float = 78000.0) -> list[Bar]:
    """Down into ``low`` and back up: one clean swing low in the middle."""
    half = n // 2
    out = []
    for i in range(n):
        d = abs(i - half) / half
        px = low + (top - low) * d
        out.append(Bar(px, px + 50, px - (0 if i == half else 10), px))
    return out


def test_two_touches_make_a_floor_one_does_not():
    bars = v_bottom(76321) + v_bottom(76493) + v_bottom(79000, top=80000)
    zones = find_zones(bars, pivot=3, min_gap=4, bounce_pct=1.0)
    floors = [z for z in zones if z.kind == FLOOR]
    assert len(floors) == 1 and floors[0].low == 76321 and floors[0].high == 76493 and floors[0].touches == 2


def test_recent_zones_are_tagged_and_block_like_the_rest():
    # three tops a few hours apart within 1.2%: the 77.7k-78.7k ceiling of 30/10
    def peak(top, n=7, floor=77000.0):
        half = n // 2
        return [Bar(p, p + (0 if i == half else -5), p - 10, p)
                for i, p in ((i, top - (top - floor) * abs(i - half) / half) for i in range(n))]
    zones = [z for z in find_recent_zones(peak(78660) + peak(77738) + peak(77958)) if z.kind == CEILING]
    assert len(zones) == 1 and zones[0].origin == "m30_48h_reciente" and zones[0].touches == 3
    assert round(zones[0].low) == 77738 and round(zones[0].high) == 78660
    assert zone_block(BUY, 77600, zones, last_h1_close=77500) == zones[0]


def test_shallow_bounce_is_not_a_zone():
    bars = v_bottom(76321, top=76500) + v_bottom(76350, top=76500)  # 0.2% bounces
    assert [z for z in find_zones(bars, pivot=3, min_gap=4, bounce_pct=1.0) if z.kind == FLOOR] == []


def test_sell_blocked_inside_floor_until_h1_closes_below():
    floor = [Zone(75500, 76500, FLOOR, 3)]
    assert zone_block(SELL, 76180, floor, last_h1_close=76600) == floor[0]   # the 18:53 short
    assert zone_block(SELL, 76700, floor, last_h1_close=76800) == floor[0]   # within 0.3% above
    assert zone_block(SELL, 77200, floor, last_h1_close=77300) is None       # well above the floor
    assert zone_block(SELL, 75300, floor, last_h1_close=75600) == floor[0]   # wick below, no H1 close yet
    assert zone_block(SELL, 75300, floor, last_h1_close=75400) is None       # confirmed break
    assert zone_block(BUY, 76180, floor, last_h1_close=76600) is None        # floors never block longs


def test_buy_blocked_under_ceiling():
    ceil = [Zone(81000, 81500, CEILING, 2)]
    assert zone_block(BUY, 80900, ceil, last_h1_close=80800) == ceil[0]
    assert zone_block(BUY, 81700, ceil, last_h1_close=81400) == ceil[0]
    assert zone_block(BUY, 81700, ceil, last_h1_close=81600) is None
    assert zone_block(SELL, 81200, ceil, last_h1_close=81200) is None


def test_manual_zones_block_both_sides():
    z = parse_manual_zones("75500-76500, junk, 82000-81500")
    assert [(m.low, m.high, m.kind) for m in z] == [(75500, 76500, BOTH), (81500, 82000, BOTH)]
    assert zone_block(SELL, 76000, z, last_h1_close=76100) == z[0]
    assert zone_block(BUY, 76000, z, last_h1_close=76100) == z[0]
