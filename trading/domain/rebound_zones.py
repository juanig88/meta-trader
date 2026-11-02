"""Rebound zones (floors and ceilings) and the entry block they impose.

The user's rule (2026-10-30): never short into a floor that keeps bouncing price (BTC
75.5k-76.5k) nor buy into a ceiling, unless the zone is broken for real: an H1 candle
closes beyond it. Zones come from H1 swing points that touched the same price area at
least twice, plus zones the user types by hand. Runbook: trading/docs/IMPULSO_M1.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from trading.domain.impulso_m1 import BUY, Bar

FLOOR = "piso"
CEILING = "techo"
BOTH = "manual"


@dataclass(frozen=True)
class Zone:
    low: float
    high: float
    kind: str
    touches: int = 0
    origin: str = "h1_60d"

    def as_dict(self) -> dict:
        return {"low": round(self.low), "high": round(self.high), "kind": self.kind, "touches": self.touches,
                "origin": self.origin}


def swing_points(bars: Sequence[Bar], pivot: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """(index, price) of swing lows and swing highs: the extreme of ``pivot`` bars each side."""
    lows, highs = [], []
    for i in range(pivot, len(bars) - pivot):
        window = bars[i - pivot:i + pivot + 1]
        if bars[i].low == min(b.low for b in window):
            lows.append((i, bars[i].low))
        if bars[i].high == max(b.high for b in window):
            highs.append((i, bars[i].high))
    return lows, highs


def _cluster(points: list[tuple[int, float]], tol_pct: float, min_touches: int, min_gap: int,
             kind: str, origin: str = "h1_60d") -> list[Zone]:
    zones: list[Zone] = []
    group: list[tuple[int, float]] = []
    for idx, px in sorted(points, key=lambda p: p[1]) + [(-1, float("inf"))]:
        if group and px > group[0][1] * (1 + tol_pct / 100):
            # distinct touches: swing points at least ``min_gap`` bars apart
            hits = sorted(i for i, _ in group)
            distinct = 1 + sum(1 for a, b in zip(hits, hits[1:]) if b - a >= min_gap)
            if distinct >= min_touches:
                zones.append(Zone(min(p for _, p in group), max(p for _, p in group), kind, distinct, origin))
            group = []
        group.append((idx, px))
    return zones


def find_zones(h1: Sequence[Bar], *, tol_pct: float = 0.8, min_touches: int = 2, pivot: int = 12,
               min_gap: int = 24, bounce_pct: float = 2.0, bounce_bars: int = 24,
               origin: str = "h1_60d") -> list[Zone]:
    """Floors from swing lows and ceilings from swing highs that repeat within ``tol_pct``.

    Only swings that bounced at least ``bounce_pct`` within ``bounce_bars`` count, and touches
    must be ``min_gap`` bars apart. Defaults (H1: half a day each side, 2% bounce, a day apart)
    leave ~14 zones over 60 days of BTC; with pivot 3 and no bounce there was one every 400 USD.
    """
    lows, highs = swing_points(h1, pivot)
    lows = [(i, p) for i, p in lows if max(b.high for b in h1[i:i + bounce_bars]) >= p * (1 + bounce_pct / 100)]
    highs = [(i, p) for i, p in highs if min(b.low for b in h1[i:i + bounce_bars]) <= p * (1 - bounce_pct / 100)]
    return (_cluster(lows, tol_pct, min_touches, min_gap, FLOOR, origin)
            + _cluster(highs, tol_pct, min_touches, min_gap, CEILING, origin))


def find_recent_zones(m30: Sequence[Bar]) -> list[Zone]:
    """Zones still forming: last ~48h of closed M30 (user's M30/H1 read, 2026-10-30).

    1.5h swings, 0.7% bounce within 6h, touches 2h apart, 1.2% grouping. On 30/10 it found
    the 77.7k-78.7k ceiling and the 76.3k-77.1k floor that the 60-day H1 pass misses.
    """
    return find_zones(m30, tol_pct=1.2, pivot=3, min_gap=4, bounce_pct=0.7, bounce_bars=12,
                      origin="m30_48h_reciente")


def parse_manual_zones(text: Optional[str]) -> list[Zone]:
    """``"75500-76500, 81000-81500"`` -> zones that act as floor and ceiling."""
    out = []
    for part in (text or "").split(","):
        lo, _, hi = part.strip().partition("-")
        try:
            a, b = float(lo), float(hi)
        except ValueError:
            continue
        out.append(Zone(min(a, b), max(a, b), BOTH, origin="manual"))
    return out


def zone_block(side: int, price: float, zones: Sequence[Zone], last_h1_close: float,
               near_pct: float = 0.3) -> Optional[Zone]:
    """The zone that forbids this entry, or None.

    Sell: blocked inside a floor or up to ``near_pct`` above it, and below it while the
    last closed H1 has not closed under the floor (no confirmed break). Buy mirrors it
    against ceilings. Manual zones count as both.
    """
    for z in zones:
        if side == BUY:
            if z.kind == FLOOR:
                continue
            if z.low * (1 - near_pct / 100) <= price <= z.high:
                return z
            if price > z.high and last_h1_close <= z.high:
                return z
        else:
            if z.kind == CEILING:
                continue
            if z.low <= price <= z.high * (1 + near_pct / 100):
                return z
            if price < z.low and last_h1_close >= z.low:
                return z
    return None
