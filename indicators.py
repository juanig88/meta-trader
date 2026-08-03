from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def ema(values: list[float], period: int) -> list[Optional[float]]:
    """
    Exponential moving average. Returns a list aligned with `values` where early values are None.
    Uses SMA(period) as the EMA seed (common practice).
    """
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if period <= 0 or n < period:
        return out

    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / float(period)
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (values[i] * k) + (prev * (1.0 - k))
        out[i] = prev
    return out


def atr(high: list[float], low: list[float], close: list[float], period: int) -> list[Optional[float]]:
    """
    Average True Range using Wilder smoothing.
    """
    n = len(close)
    out: list[Optional[float]] = [None] * n
    if period <= 0 or n < period + 1:
        return out

    tr: list[float] = [0.0] * n
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    # Wilder seed
    seed = sum(tr[1 : period + 1]) / float(period)
    out[period] = seed
    prev = seed
    for i in range(period + 1, n):
        prev = ((prev * (period - 1)) + tr[i]) / float(period)
        out[i] = prev
    return out


@dataclass(frozen=True)
class AdxResult:
    plus_di: list[Optional[float]]
    minus_di: list[Optional[float]]
    adx: list[Optional[float]]


def adx_wilder(
    high: list[float],
    low: list[float],
    close: list[float],
    period: int = 14,
) -> AdxResult:
    """
    ADX (Wilder) with DI+/DI-.
    Returns series aligned with inputs; early values are None.
    """
    n = len(close)
    plus_di: list[Optional[float]] = [None] * n
    minus_di: list[Optional[float]] = [None] * n
    adx: list[Optional[float]] = [None] * n
    if period <= 0 or n < period + 2:
        return AdxResult(plus_di=plus_di, minus_di=minus_di, adx=adx)

    tr: list[float] = [0.0] * n
    plus_dm: list[float] = [0.0] * n
    minus_dm: list[float] = [0.0] * n

    for i in range(1, n):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    # Wilder smoothing seeds at index=period
    tr14 = sum(tr[1 : period + 1])
    p14 = sum(plus_dm[1 : period + 1])
    m14 = sum(minus_dm[1 : period + 1])

    if tr14 <= 0:
        return AdxResult(plus_di=plus_di, minus_di=minus_di, adx=adx)

    def di(dm: float, trv: float) -> float:
        return 100.0 * (dm / trv) if trv > 0 else 0.0

    pdi = di(p14, tr14)
    mdi = di(m14, tr14)
    plus_di[period] = pdi
    minus_di[period] = mdi

    dx: list[Optional[float]] = [None] * n

    denom = pdi + mdi
    dx[period] = (100.0 * abs(pdi - mdi) / denom) if denom > 0 else 0.0

    # Continue smoothing
    tr_s = tr14
    p_s = p14
    m_s = m14

    for i in range(period + 1, n):
        tr_s = tr_s - (tr_s / period) + tr[i]
        p_s = p_s - (p_s / period) + plus_dm[i]
        m_s = m_s - (m_s / period) + minus_dm[i]

        pdi = di(p_s, tr_s)
        mdi = di(m_s, tr_s)
        plus_di[i] = pdi
        minus_di[i] = mdi

        denom = pdi + mdi
        dx[i] = (100.0 * abs(pdi - mdi) / denom) if denom > 0 else 0.0

    # ADX is Wilder-smoothed DX
    # Seed ADX at index = 2*period as the average of DX from period..2*period-1
    seed_idx = 2 * period
    if n <= seed_idx:
        return AdxResult(plus_di=plus_di, minus_di=minus_di, adx=adx)

    seed_vals: list[float] = [float(v) for v in dx[period:seed_idx] if v is not None]
    if len(seed_vals) < period:
        return AdxResult(plus_di=plus_di, minus_di=minus_di, adx=adx)

    seed_adx = sum(seed_vals[-period:]) / float(period)
    adx[seed_idx - 1] = seed_adx
    prev = seed_adx
    for i in range(seed_idx, n):
        if dx[i] is None:
            continue
        prev = ((prev * (period - 1)) + float(dx[i])) / float(period)
        adx[i] = prev

    return AdxResult(plus_di=plus_di, minus_di=minus_di, adx=adx)

