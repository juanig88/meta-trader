"""Labels from the rest of the trend chapter. Pure: no orders.

Speed rays, a reversal day, a gap, a 50-bar average and tick-volume ratio are
recorded so a later pass can see them. None of them is a second entry rule.
The 50-bar length and the 25% close of a reversal day are ours: the text says
"near the extreme" and "use an average when the line gets too steep" without
a number we can copy into an order.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

SMA_BARS = 50
REVERSAL_CLOSE_FRAC = 0.25
VOLUME_LOOKBACK = 20
# Fractions of the impulse slope. The 1x1 ray is the move itself.
RAY_RATIOS: tuple[float, ...] = (0.125, 0.25, 1.0 / 3.0, 0.382, 0.5, 0.618, 2.0 / 3.0, 1.0, 2.0, 3.0)


def ray_at(
    origin: float,
    extreme: float,
    fraction: float,
    origin_index: int,
    extreme_index: int,
    index: int,
) -> Optional[float]:
    """Price of the ray that reaches ``fraction`` of the impulse at its end."""
    span_i = int(extreme_index) - int(origin_index)
    if span_i == 0:
        return None
    target = float(origin) + (float(extreme) - float(origin)) * float(fraction)
    slope = (target - float(origin)) / span_i
    return float(origin) + slope * (int(index) - int(origin_index))


def fan_rays(
    origin: float,
    extreme: float,
    origin_index: int,
    extreme_index: int,
    index: int,
) -> dict[str, Optional[float]]:
    return {
        f"{ratio:.3f}": ray_at(origin, extreme, ratio, origin_index, extreme_index, index)
        for ratio in RAY_RATIOS
    }


def sma(closes: Sequence[float], period: int = SMA_BARS) -> Optional[float]:
    n = int(period)
    if n <= 0 or len(closes) < n:
        return None
    window = [float(x) for x in closes[-n:]]
    return sum(window) / n


def reversal_day(
    prev_high: float,
    prev_low: float,
    high: float,
    low: float,
    close: float,
) -> Optional[str]:
    rng = float(high) - float(low)
    if rng <= 0.0:
        return None
    near_low = float(close) <= float(low) + REVERSAL_CLOSE_FRAC * rng
    near_high = float(close) >= float(high) - REVERSAL_CLOSE_FRAC * rng
    if float(high) > float(prev_high) and near_low:
        return "cambio_bajista"
    if float(low) < float(prev_low) and near_high:
        return "cambio_alcista"
    return None


def gap(prev_high: float, prev_low: float, high: float, low: float) -> Optional[str]:
    if float(low) > float(prev_high):
        return "hueco_alza"
    if float(high) < float(prev_low):
        return "hueco_baja"
    return None


def volume_ratio(volumes: Sequence[float], lookback: int = VOLUME_LOOKBACK) -> Optional[float]:
    n = int(lookback)
    if n <= 0 or len(volumes) < n + 1:
        return None
    base_rows = [float(v) for v in volumes[-n - 1 : -1]]
    base = sum(base_rows) / n
    if base <= 0.0:
        return None
    return float(volumes[-1]) / base


def bar_tools(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Optional[Sequence[float]] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "sma50": sma(closes),
        "dia_de_cambio": None,
        "hueco": None,
        "volumen_ratio": None,
    }
    if len(closes) >= 2:
        out["dia_de_cambio"] = reversal_day(highs[-2], lows[-2], highs[-1], lows[-1], closes[-1])
        out["hueco"] = gap(highs[-2], lows[-2], highs[-1], lows[-1])
    if volumes is not None and len(volumes) == len(closes):
        out["volumen_ratio"] = volume_ratio(volumes)
    return out


def r_multiple(entry: float, exit_price: float, risk: float, side: str) -> Optional[float]:
    if risk <= 0.0 or side not in ("BUY", "SELL"):
        return None
    move = (float(exit_price) - float(entry)) if side == "BUY" else (float(entry) - float(exit_price))
    return move / float(risk)


def stop_fill(side: str, stop: float, open_: float, high: float, low: float) -> Optional[float]:
    """Fill of a structural stop on one bar. A gap through the stop fills at the open."""
    if side == "BUY":
        if float(open_) <= float(stop):
            return float(open_)
        if float(low) <= float(stop):
            return float(stop)
        return None
    if side == "SELL":
        if float(open_) >= float(stop):
            return float(open_)
        if float(high) >= float(stop):
            return float(stop)
    return None
