"""TP chico lateral without Mondays or 00-05 UTC: forward test on the Exness demo. Pure: no MT5, no clock.

PREREG_TP_LATERAL_DEMO.md. At each closed M15 candle, with no position of its own:

- not Monday and not 00:00-05:59 UTC
- within 1% of a zone, no candle in the last 4 M15 over 2.5x the median of the 96 before, and no
  1% move in the last 16 M15
- side of the daily EMA 20; TP 100 / SL 1000 of price from the fill (``tp_chico.levels``)
- stop for the UTC day at -20 USD; end at 100 trades or -50 USD

Runbook: trading/docs/TP_CHICO.md
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import median
from typing import Optional, Sequence

from trading.domain.rebound_zones import Zone

ZONE_MAX = 0.01
BIG_MULT, BIG_LAST, BIG_REF = 2.5, 4, 96
RUN_BARS, RUN_MAX = 16, 0.01
DAY_LOSS_USD = 20.0
MAX_TRADES = 100
MAX_LOSS_USD = 50.0

BLOCK_MONDAY = "lunes"
BLOCK_NIGHT = "00_05_utc"
BLOCK_FAR = "lejos_de_zona"
BLOCK_BIG = "vela_grande"
BLOCK_RUN = "disparo_4h"
BLOCK_DAY = "tope_diario"
BLOCK_DONE = "terminado"


@dataclass
class LateralState:
    trades: int = 0
    wins: int = 0
    net_usd: float = 0.0
    day: str = ""
    day_net_usd: float = 0.0
    day_stops: int = 0
    finished: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "LateralState":
        return cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})


def time_block(ts: datetime) -> str:
    if ts.weekday() == 0:
        return BLOCK_MONDAY
    if ts.hour < 6:
        return BLOCK_NIGHT
    return ""


def zone_distance(price: float, zones: Sequence[Zone]) -> Optional[float]:
    """Fraction from the price to the nearest zone edge (0 inside one); None without zones."""
    if not zones or price <= 0:
        return None
    return min(0.0 if z.low <= price <= z.high else min(abs(price - z.low), abs(price - z.high)) / price
               for z in zones)


def calm_block(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> str:
    """Closed M15 candles, oldest first. Empty string when calm."""
    need = max(BIG_REF + BIG_LAST, RUN_BARS + 1)
    if len(closes) < need:
        return BLOCK_BIG
    rng = [h - l for h, l in zip(highs, lows)]
    ref = median(rng[-(BIG_LAST + BIG_REF):-BIG_LAST])
    if ref <= 0 or max(rng[-BIG_LAST:]) > BIG_MULT * ref:
        return BLOCK_BIG
    if abs(closes[-1] / closes[-1 - RUN_BARS] - 1) > RUN_MAX:
        return BLOCK_RUN
    return ""


def roll_day(state: LateralState, day: str) -> None:
    if state.day != day:
        state.day, state.day_net_usd = day, 0.0


def entry_block(state: LateralState, ts: datetime, zone_dist: Optional[float], calm: str) -> str:
    """The first reason not to enter, in the pre-registered order; empty string to enter."""
    roll_day(state, ts.strftime("%Y-%m-%d"))
    if state.finished:
        return BLOCK_DONE
    if state.day_net_usd <= -DAY_LOSS_USD:
        return BLOCK_DAY
    t = time_block(ts)
    if t:
        return t
    if zone_dist is None or zone_dist > ZONE_MAX:
        return BLOCK_FAR
    return calm


def record_close(state: LateralState, net_usd: float, day: str) -> None:
    roll_day(state, day)
    state.trades += 1
    state.wins += int(net_usd > 0)
    state.net_usd = round(state.net_usd + net_usd, 2)
    state.day_net_usd = round(state.day_net_usd + net_usd, 2)
    if state.day_net_usd <= -DAY_LOSS_USD:
        state.day_stops += 1
    if state.trades >= MAX_TRADES:
        state.finished = f"{MAX_TRADES}_operaciones"
    elif state.net_usd <= -MAX_LOSS_USD:
        state.finished = f"perdida_{int(MAX_LOSS_USD)}_usd"
