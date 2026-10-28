"""Extra alpha diagnostics: raw Z-score, Bollinger squeeze, relative volume, RSI.

Pure functions over sequences. No pandas, numpy, or scipy: the live process
does not depend on that stack, and domain code stays free of third-party
libraries. The normal CDF is math.erf, the same function scipy.stats.norm.cdf
uses. Series helpers walk the window once, which is the vectorised form of
these indicators for the bar counts this engine actually scores.

None of these values is a calibrated win rate. q is a monotone map of the
multifactor score into (0, 1) so a later, validated Kelly step can consume a
probability. It is computed before net edge. This module never sizes a trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence


def _clean(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    for v in values or ():
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")):
            continue
        out.append(f)
    return out


def normal_cdf(x: float) -> float:
    """Standard-normal CDF. Matches scipy.stats.norm.cdf."""
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def normal_two_tailed_survival(z: float) -> float:
    """P(|Z| >= |z|) under N(0, 1). A stretch diagnostic, not a win probability."""
    az = abs(float(z))
    if az != az or az == float("inf"):
        return 0.0
    return max(0.0, min(1.0, 2.0 * (1.0 - normal_cdf(az))))


def directional_probability(score: float, temperature: float = 1.0) -> float:
    """Map a signed score onto (0, 1). 0 -> 0.5. Not a historical win rate."""
    temp = float(temperature)
    if temp <= 0.0:
        return 0.5
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 0.5
    if s != s or s in (float("inf"), float("-inf")):
        return 0.5
    return 0.5 * (1.0 + math.tanh(s / temp))


def price_zscore(closes: Sequence[float], *, lookback: int = 50) -> Optional[float]:
    """(price - linearly weighted MA) / sample stdev of the same window.

    Positive means the last close is stretched above its recent mean.
    """
    vals = _clean(closes)
    lb = int(lookback)
    if lb < 2 or len(vals) < lb:
        return None
    window = vals[-lb:]
    denom = lb * (lb + 1) / 2.0
    wma = sum(v * (i + 1) for i, v in enumerate(window)) / denom
    mean = sum(window) / lb
    var = sum((v - mean) ** 2 for v in window) / (lb - 1)
    if var <= 0.0:
        return None
    return (window[-1] - wma) / math.sqrt(var)


def _population_stdev(window: Sequence[float]) -> float:
    n = len(window)
    if n < 2:
        return 0.0
    mean = sum(window) / n
    return math.sqrt(sum((v - mean) ** 2 for v in window) / n)


@dataclass(frozen=True)
class BandwidthState:
    middle: float
    upper: float
    lower: float
    bandwidth: float
    bandwidth_percentile: Optional[float]
    squeeze: Optional[bool]


def bollinger_bandwidth(
    closes: Sequence[float],
    *,
    period: int = 20,
    n_std: float = 2.0,
    rank_lookback: int = 120,
    squeeze_quantile: float = 0.20,
) -> Optional[BandwidthState]:
    """Bandwidth (upper-lower)/middle and whether it sits in the lower tail.

    Bollinger stdev is the population stdev (divide by N), which is the
    classic definition. Squeeze is true only when the current bandwidth's
    percentile rank versus prior windows is at or below squeeze_quantile.
    With too little history the percentile and the squeeze flag are None
    rather than a false "not squeezed".
    """
    vals = _clean(closes)
    p = int(period)
    if p < 2 or len(vals) < p:
        return None
    width = float(n_std)
    if width < 0.0:
        return None

    bands: list[float] = []
    for end in range(p, len(vals) + 1):
        window = vals[end - p : end]
        middle = sum(window) / p
        if middle == 0.0:
            continue
        sd = _population_stdev(window)
        bands.append((2.0 * width * sd) / abs(middle))
    if not bands:
        return None

    window = vals[-p:]
    middle = sum(window) / p
    sd = _population_stdev(window)
    upper = middle + width * sd
    lower = middle - width * sd
    bandwidth = bands[-1]

    prior = bands[-1 - int(rank_lookback) : -1] if int(rank_lookback) > 0 else []
    percentile: Optional[float] = None
    squeeze: Optional[bool] = None
    if len(prior) >= max(p, 5):
        percentile = sum(1 for b in prior if b <= bandwidth) / float(len(prior))
        squeeze = percentile <= float(squeeze_quantile)

    return BandwidthState(
        middle=middle,
        upper=upper,
        lower=lower,
        bandwidth=bandwidth,
        bandwidth_percentile=percentile,
        squeeze=squeeze,
    )


# MetaTrader5 tick flags. Buy and sell are mutually exclusive on a trade tick.
TICK_FLAG_BUY = 32
TICK_FLAG_SELL = 64


def volume_delta(volumes: Sequence[float], flags: Sequence[int]) -> Optional[float]:
    """(buy volume - sell volume) / (buy + sell), in [-1, 1].

    Ticks with neither flag are ignored. None when the window has no signed
    volume, so a missing tape is not reported as a balanced book.
    """
    buy = 0.0
    sell = 0.0
    n = min(len(volumes or ()), len(flags or ()))
    if n <= 0:
        return None
    for i in range(n):
        try:
            vol = float(volumes[i])
            flag = int(flags[i])
        except (TypeError, ValueError):
            continue
        if vol != vol or vol <= 0.0:
            continue
        buy_bit = bool(flag & TICK_FLAG_BUY)
        sell_bit = bool(flag & TICK_FLAG_SELL)
        if buy_bit and not sell_bit:
            buy += vol
        elif sell_bit and not buy_bit:
            sell += vol
    total = buy + sell
    if total <= 0.0:
        return None
    return (buy - sell) / total


def relative_volume(volumes: Sequence[float], *, lookback: int = 20) -> Optional[float]:
    """Last bar volume divided by the mean of the previous `lookback` bars.

    1.0 is an average bar. Below 1 is thin participation. The current bar is
    excluded from the baseline so a spike does not dilute itself.
    """
    vals = _clean(volumes)
    lb = int(lookback)
    if lb < 1 or len(vals) < lb + 1:
        return None
    baseline = vals[-(lb + 1) : -1]
    avg = sum(baseline) / len(baseline) if baseline else 0.0
    if avg <= 0.0:
        return None
    return vals[-1] / avg


def wilder_rsi_series(closes: Sequence[float], period: int = 14) -> list[Optional[float]]:
    """Wilder RSI aligned with `closes`. Early values are None."""
    vals = _clean(closes)
    n = len(vals)
    p = int(period)
    out: list[Optional[float]] = [None] * n
    if p <= 0 or n < p + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        diff = vals[i] - vals[i - 1]
        gains[i] = max(diff, 0.0)
        losses[i] = max(-diff, 0.0)
    avg_g = sum(gains[1 : p + 1]) / p
    avg_l = sum(losses[1 : p + 1]) / p
    out[p] = _rsi_from_averages(avg_g, avg_l)
    for i in range(p + 1, n):
        avg_g = (avg_g * (p - 1) + gains[i]) / p
        avg_l = (avg_l * (p - 1) + losses[i]) / p
        out[i] = _rsi_from_averages(avg_g, avg_l)
    return out


def _rsi_from_averages(avg_g: float, avg_l: float) -> float:
    if avg_g == 0.0 and avg_l == 0.0:
        return 50.0
    if avg_l == 0.0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass(frozen=True)
class RsiState:
    rsi: Optional[float]
    velocity: Optional[float]
    velocity_atr: Optional[float]


def rsi_state(
    closes: Sequence[float],
    atr_value: Optional[float] = None,
    *,
    period: int = 14,
    lag: int = 3,
) -> RsiState:
    """Last RSI, its per-bar change, and that change scaled by ATR/price.

    velocity_atr is large when RSI is moving fast relative to current range.
    It is a exhaustion diagnostic, not an entry signal by itself.
    """
    series = wilder_rsi_series(closes, period)
    defined = [(i, v) for i, v in enumerate(series) if v is not None]
    if not defined:
        return RsiState(None, None, None)
    last_i, last = defined[-1]
    step = int(lag)
    velocity: Optional[float] = None
    if step > 0:
        prev = next((v for i, v in reversed(defined) if i <= last_i - step), None)
        if prev is not None:
            velocity = (float(last) - float(prev)) / float(step)
    vel_atr: Optional[float] = None
    vals = _clean(closes)
    if velocity is not None and atr_value and vals and vals[-1] > 0.0:
        atr_pct = float(atr_value) / float(vals[-1])
        if atr_pct > 0.0:
            vel_atr = float(velocity) / (atr_pct * 100.0)
    return RsiState(float(last), velocity, vel_atr)


def apply_context_adjustments(
    score: float,
    *,
    rvol: Optional[float],
    momentum: Optional[float],
    squeeze: Optional[bool],
    flow: Optional[float],
    fakeout_rvol: float = 0.60,
    fakeout_momentum: float = 0.35,
    fakeout_damp: float = 0.65,
    squeeze_boost: float = 1.10,
    squeeze_flow_min: float = 0.10,
) -> tuple[float, dict]:
    """Damp a move that printed without volume; mildly boost a confirmed squeeze.

    Both multipliers are bounded and the result stays inside [-1, 1]. They
    adjust the shadow score only. They do not place orders.
    """
    try:
        out = float(score)
    except (TypeError, ValueError):
        out = 0.0
    if out != out or out in (float("inf"), float("-inf")):
        out = 0.0
    base = out
    notes: list[str] = []
    if (
        rvol is not None
        and momentum is not None
        and float(rvol) < float(fakeout_rvol)
        and abs(float(momentum)) >= float(fakeout_momentum)
    ):
        out *= float(fakeout_damp)
        notes.append("fakeout_damp")
    same_sign = flow is not None and out != 0.0 and ((float(flow) > 0.0) == (out > 0.0))
    if (
        squeeze is True
        and flow is not None
        and abs(float(flow)) >= float(squeeze_flow_min)
        and same_sign
    ):
        out *= float(squeeze_boost)
        notes.append("squeeze_boost")
    out = max(-1.0, min(1.0, out))
    return out, {"notes": notes, "base_score": base, "adjusted_score": out}
