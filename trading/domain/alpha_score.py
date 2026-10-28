"""Multi-factor alpha scoring. Pure: no I/O, no MT5, no DB.

Four factors, combined into a signed score in roughly [-1, 1], then charged
the real cost of trading to produce a net edge.

    momentum    trend displacement measured in ATR units, so a 200-point move
                in gold and a 20-pip move in USDJPY are comparable
    volatility  range expansion aligned with the move. Expanding range in the
                direction of price confirms a trend; expanding range against
                it is whipsaw, so this factor is directional rather than a
                bare magnitude
    zscore      distance from a weighted moving average, entered with a
                NEGATIVE sign. This is deliberate: it is an anti-chase term,
                matching the BOT_ANTI_CHASE_* filters the live bot already
                uses. Momentum says "go with the trend", zscore says "but not
                after it has already stretched three sigma"
    flow        tick volume relative to its recent average, signed by the bar
                body, optionally refined by bid/ask imbalance from raw ticks

Each factor is squashed with tanh, so no single one can dominate the sum and
an outlier bar cannot produce an unbounded score.

On the edge estimate: gross_edge is |score| x ATR in points. That is a
confidence-scaled upper bound on the available move, NOT a calibrated return
forecast. It exists so costs can be subtracted in the same units and a setup
that cannot pay for its own spread is rejected. Treating it as a predicted
profit would be exactly the kind of overfit story this repo's risk rules
exist to block.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from indicators import atr as atr_series
from indicators import ema as ema_series
from trading.domain.quant_factors import (
    apply_context_adjustments,
    bollinger_bandwidth,
    directional_probability,
    normal_two_tailed_survival,
    price_zscore,
    relative_volume,
    rsi_state,
    volume_delta,
)

DIRECTION_BUY = "BUY"
DIRECTION_SELL = "SELL"
DIRECTION_NONE = "NONE"


@dataclass(frozen=True)
class AlphaWeights:
    momentum: float = 0.35
    volatility: float = 0.15
    zscore: float = 0.30
    flow: float = 0.20

    def as_dict(self) -> dict[str, float]:
        return {
            "momentum": float(self.momentum),
            "volatility": float(self.volatility),
            "zscore": float(self.zscore),
            "flow": float(self.flow),
        }


@dataclass(frozen=True)
class CostModel:
    """Costs in points. Spread is observed live; the rest are broker estimates."""

    spread_points: float = 0.0
    commission_points: float = 0.0
    slippage_points: float = 0.0

    @property
    def total_points(self) -> float:
        return (
            max(0.0, float(self.spread_points))
            + max(0.0, float(self.commission_points))
            + max(0.0, float(self.slippage_points))
        )


@dataclass(frozen=True)
class AlphaScore:
    symbol: str
    timeframe: str
    direction: str
    score: float
    momentum: Optional[float] = None
    volatility: Optional[float] = None
    zscore: Optional[float] = None
    flow: Optional[float] = None
    gross_edge: float = 0.0
    cost_spread: float = 0.0
    cost_commission: float = 0.0
    cost_slippage: float = 0.0
    net_edge: float = 0.0
    q: float = 0.5
    features: dict = field(default_factory=dict)

    def as_row(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "score": self.score,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "zscore": self.zscore,
            "flow": self.flow,
            "gross_edge": self.gross_edge,
            "cost_spread": self.cost_spread,
            "cost_commission": self.cost_commission,
            "cost_slippage": self.cost_slippage,
            "net_edge": self.net_edge,
            "features": dict(self.features),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean(values: Sequence[float]) -> list[float]:
    """Drop None and NaN. A single bad bar must not poison a whole series."""
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


def squash(value: float, scale: float = 1.0) -> float:
    """tanh squash into [-1, 1]. scale is the input value mapped to ~0.76."""
    s = float(scale)
    if s <= 0.0:
        return 0.0
    return math.tanh(float(value) / s)


def weighted_moving_average(values: Sequence[float], period: int) -> Optional[float]:
    """Linearly weighted MA of the last `period` values (most recent weighs most)."""
    vals = _clean(values)
    p = int(period)
    if p <= 0 or len(vals) < p:
        return None
    window = vals[-p:]
    denom = p * (p + 1) / 2.0
    return sum(v * (i + 1) for i, v in enumerate(window)) / denom


def stdev(values: Sequence[float]) -> Optional[float]:
    vals = _clean(values)
    n = len(vals)
    if n < 2:
        return None
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    return math.sqrt(var)


# ---------------------------------------------------------------------------
# Factors. Each returns None when it cannot be computed honestly.
# ---------------------------------------------------------------------------


def momentum_factor(
    closes: Sequence[float],
    atr_value: Optional[float],
    *,
    ema_period: int = 20,
    atr_scale: float = 1.5,
) -> Optional[float]:
    """Displacement of price from its EMA, measured in ATR units."""
    vals = _clean(closes)
    if len(vals) < ema_period or not atr_value or float(atr_value) <= 0.0:
        return None
    series = ema_series(vals, int(ema_period))
    anchor = series[-1] if series else None
    if anchor is None:
        return None
    return squash((vals[-1] - float(anchor)) / float(atr_value), atr_scale)


def volatility_factor(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    fast_period: int = 5,
    slow_period: int = 20,
    scale: float = 0.5,
) -> Optional[float]:
    """Range expansion signed by the direction of the move.

    Expansion with price = trend confirmation. Expansion against price =
    whipsaw. A bare volatility magnitude has no sign and could not enter a
    signed weighted sum without smuggling in the direction of another factor.
    """
    h, low, c = _clean(highs), _clean(lows), _clean(closes)
    n = min(len(h), len(low), len(c))
    if n < slow_period + 2:
        return None
    h, low, c = h[-n:], low[-n:], c[-n:]

    fast = atr_series(h, low, c, int(fast_period))
    slow = atr_series(h, low, c, int(slow_period))
    fast_v = fast[-1] if fast else None
    slow_v = slow[-1] if slow else None
    if not fast_v or not slow_v or float(slow_v) <= 0.0:
        return None

    expansion = (float(fast_v) / float(slow_v)) - 1.0
    direction = c[-1] - c[-min(len(c), int(fast_period))]
    if direction == 0.0:
        return 0.0
    return squash(math.copysign(expansion, direction), scale)


def zscore_factor(
    closes: Sequence[float],
    *,
    lookback: int = 50,
    scale: float = 2.0,
) -> Optional[float]:
    """Anti-chase term: NEGATIVE of the standardised distance from the WMA.

    A price stretched far above its mean returns a negative (sell-leaning)
    value, which damps a momentum signal that has already run.
    """
    vals = _clean(closes)
    lb = int(lookback)
    if lb < 2 or len(vals) < lb:
        return None
    wma = weighted_moving_average(vals, lb)
    sd = stdev(vals[-lb:])
    if wma is None or sd is None or sd <= 0.0:
        # Zero dispersion: a flat series has no meaningful stretch.
        return None
    z = (vals[-1] - wma) / sd
    return -squash(z, scale)


def flow_factor(
    opens: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    *,
    lookback: int = 20,
    scale: float = 0.75,
) -> Optional[float]:
    """Volume surge signed by the body of the most recent bar."""
    o, c, v = _clean(opens), _clean(closes), _clean(volumes)
    n = min(len(o), len(c), len(v))
    lb = int(lookback)
    if n < lb + 1 or lb < 2:
        return None
    o, c, v = o[-n:], c[-n:], v[-n:]

    baseline = v[-(lb + 1) : -1]
    avg = sum(baseline) / len(baseline) if baseline else 0.0
    if avg <= 0.0:
        return None

    surge = (v[-1] / avg) - 1.0
    body = c[-1] - o[-1]
    if body == 0.0:
        return 0.0
    return squash(math.copysign(surge, body), scale)


def tick_imbalance(
    bids: Sequence[float],
    asks: Sequence[float],
    *,
    min_ticks: int = 20,
) -> Optional[float]:
    """Signed uptick/downtick imbalance of the mid price, in [-1, 1].

    Refines the bar-level flow factor with actual bid/ask movement when raw
    ticks are available.
    """
    b, a = _clean(bids), _clean(asks)
    n = min(len(b), len(a))
    if n < max(2, int(min_ticks)):
        return None
    mids = [(b[i] + a[i]) / 2.0 for i in range(n)]
    up = down = 0
    for i in range(1, n):
        if mids[i] > mids[i - 1]:
            up += 1
        elif mids[i] < mids[i - 1]:
            down += 1
    decided = up + down
    if decided == 0:
        return 0.0
    return (up - down) / float(decided)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def combine(
    factors: dict[str, Optional[float]],
    weights: AlphaWeights,
) -> tuple[float, dict[str, float]]:
    """Weighted sum over the factors that could be computed.

    Weights are renormalised across the available factors. Treating a missing
    factor as 0.0 would silently drag the score toward neutral and make a
    partially-computable setup look weaker than the evidence says.
    """
    wmap = weights.as_dict()
    usable = {
        name: float(value)
        for name, value in factors.items()
        if value is not None and float(wmap.get(name, 0.0)) > 0.0
    }
    if not usable:
        return 0.0, {}
    total_w = sum(wmap[name] for name in usable)
    if total_w <= 0.0:
        return 0.0, {}
    applied = {name: wmap[name] / total_w for name in usable}
    score = sum(usable[name] * applied[name] for name in usable)
    return max(-1.0, min(1.0, score)), applied


def score_bars(
    *,
    symbol: str,
    timeframe: str,
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float] = (),
    point: float,
    costs: CostModel = CostModel(),
    weights: AlphaWeights = AlphaWeights(),
    atr_period: int = 14,
    ema_period: int = 20,
    zscore_lookback: int = 50,
    flow_lookback: int = 20,
    min_score: float = 0.15,
    tick_bids: Sequence[float] = (),
    tick_asks: Sequence[float] = (),
    tick_volumes: Sequence[float] = (),
    tick_flags: Sequence[int] = (),
) -> AlphaScore:
    """Score one symbol/timeframe from OHLCV, charging real costs."""
    o, h, low, c = _clean(opens), _clean(highs), _clean(lows), _clean(closes)
    v = _clean(volumes)

    atr_list = atr_series(h, low, c, int(atr_period)) if min(len(h), len(low), len(c)) else []
    atr_value = atr_list[-1] if atr_list else None

    imbalance = tick_imbalance(tick_bids, tick_asks)
    bar_flow = flow_factor(o, c, v, lookback=flow_lookback)
    if imbalance is not None and bar_flow is not None:
        flow = (bar_flow + imbalance) / 2.0
    else:
        flow = bar_flow if bar_flow is not None else imbalance

    factors: dict[str, Optional[float]] = {
        "momentum": momentum_factor(c, atr_value, ema_period=ema_period),
        "volatility": volatility_factor(h, low, c),
        "zscore": zscore_factor(c, lookback=zscore_lookback),
        "flow": flow,
    }
    base_score, applied = combine(factors, weights)

    raw_z = price_zscore(c, lookback=zscore_lookback)
    bb = bollinger_bandwidth(c)
    rvol = relative_volume(v, lookback=flow_lookback)
    delta = volume_delta(tick_volumes, tick_flags)
    rsi = rsi_state(c, atr_value)
    score, adjustments = apply_context_adjustments(
        base_score,
        rvol=rvol,
        momentum=factors["momentum"],
        squeeze=None if bb is None else bb.squeeze,
        flow=factors["flow"],
    )
    # q is a monotone map of the adjusted score. It is not a win rate and it
    # is not fed to Kelly. Net edge is computed after q, from the same score,
    # so a setup that cannot pay the spread is still rejected.
    q = directional_probability(score)

    pt = float(point) if point and float(point) > 0.0 else 0.0
    atr_points = (float(atr_value) / pt) if (atr_value and pt > 0.0) else 0.0
    gross_edge = abs(score) * atr_points

    cost_spread = max(0.0, float(costs.spread_points))
    cost_commission = max(0.0, float(costs.commission_points))
    cost_slippage = max(0.0, float(costs.slippage_points))
    net_edge = gross_edge - (cost_spread + cost_commission + cost_slippage)

    direction = DIRECTION_NONE
    if abs(score) >= float(min_score) and net_edge > 0.0:
        direction = DIRECTION_BUY if score > 0 else DIRECTION_SELL

    return AlphaScore(
        symbol=str(symbol).strip().upper(),
        timeframe=str(timeframe).strip().upper(),
        direction=direction,
        score=score,
        momentum=factors["momentum"],
        volatility=factors["volatility"],
        zscore=factors["zscore"],
        flow=factors["flow"],
        gross_edge=gross_edge,
        cost_spread=cost_spread,
        cost_commission=cost_commission,
        cost_slippage=cost_slippage,
        net_edge=net_edge,
        q=q,
        features={
            "bars": len(c),
            "atr": atr_value,
            "atr_points": atr_points,
            "point": pt,
            "applied_weights": applied,
            "tick_imbalance": imbalance,
            "min_score": float(min_score),
            "base_score": base_score,
            "q": q,
            "price_zscore": raw_z,
            "z_tail": None if raw_z is None else normal_two_tailed_survival(raw_z),
            "bollinger_bandwidth": None if bb is None else bb.bandwidth,
            "bollinger_bandwidth_pct": None if bb is None else bb.bandwidth_percentile,
            "squeeze": None if bb is None else bb.squeeze,
            "rvol": rvol,
            "volume_delta": delta,
            "rsi": rsi.rsi,
            "rsi_velocity": rsi.velocity,
            "rsi_velocity_atr": rsi.velocity_atr,
            "adjustments": adjustments,
            "kelly_applied": False,
        },
    )
