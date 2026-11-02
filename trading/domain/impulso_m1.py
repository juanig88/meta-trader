"""M1 impulse rule written from the user's manual BTC trades of 2026-10-29/30.

Entry (all at the moment of deciding, nothing in hindsight):

- the last ``push_bars`` closed M1 candles all close in the same direction
- the push (open of the first to close of the last) is at least the high-low range
  of the ``range_bars`` closed M1 candles before it: sideways, then a shot
- the forming M1 candle keeps going: price is beyond the last push close
- at least ``min_tf_agree`` of the forming M5/M15/M30/H1/H4 candles are on that side
  (price beyond their open)

Stop: the other side of the sideways range, plus a buffer. Exit variants live in the
runner: a fixed USD take-profit, or ``fade_exit`` (momentum gone while in profit).
Runbook: trading/docs/IMPULSO_M1.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

BUY = 1
SELL = -1


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ImpulseParams:
    push_bars: int = 3
    range_bars: int = 12
    min_tf_agree: int = 4
    sl_buffer: float = 0.0  # price units added beyond the range extreme
    spike_mult: float = 3.0  # skip when the last push candle is this many times a normal M1 (0 = off)


@dataclass(frozen=True)
class ImpulseSignal:
    side: int
    price: float
    sl: float
    push: float
    range_width: float
    tf_agree: int

    def as_dict(self) -> dict:
        return {"side": "buy" if self.side == BUY else "sell", "price": self.price, "sl": self.sl,
                "push": round(self.push, 2), "range_width": round(self.range_width, 2),
                "tf_agree": self.tf_agree}


def detect_impulse(closed_m1: Sequence[Bar], price: float, forming_opens: Sequence[float],
                   params: ImpulseParams = ImpulseParams()) -> Optional[ImpulseSignal]:
    """``closed_m1`` oldest first, last item is the most recent closed M1 bar.

    ``forming_opens`` are the opens of the candles currently forming on the higher
    timeframes (M5, M15, M30, H1, H4); ``price`` is the live price on the side we'd trade.
    """
    need = params.push_bars + params.range_bars
    if len(closed_m1) < need:
        return None
    push = closed_m1[-params.push_bars:]
    rng = closed_m1[-need:-params.push_bars]
    for side in (BUY, SELL):
        if not all((b.close - b.open) * side > 0 for b in push):
            continue
        move = (push[-1].close - push[0].open) * side
        width = max(b.high for b in rng) - min(b.low for b in rng)
        if move <= 0 or move < width:
            continue
        if (price - push[-1].close) * side <= 0:
            continue
        if params.spike_mult > 0 and is_exhaustion(push[-1], rng, params.spike_mult):
            continue
        agree = sum(1 for o in forming_opens if (price - o) * side > 0)
        if agree < params.min_tf_agree:
            continue
        if side == BUY:
            sl = min(b.low for b in rng) - params.sl_buffer
        else:
            sl = max(b.high for b in rng) + params.sl_buffer
        return ImpulseSignal(side=side, price=price, sl=sl, push=move, range_width=width, tf_agree=agree)
    return None


def is_exhaustion(candle: Bar, reference: Sequence[Bar], mult: float) -> bool:
    """A climax candle: high-low at least ``mult`` times the median of ``reference`` (18:52 on 30/10: 3.8x)."""
    sizes = sorted(b.high - b.low for b in reference)
    if not sizes:
        return False
    median = sizes[len(sizes) // 2]
    return median > 0 and (candle.high - candle.low) >= mult * median


@dataclass(frozen=True)
class RejectionParams:
    pullback_bars: int = 3
    min_tf_agree: int = 4
    sl_buffer: float = 0.0


def detect_rejection(closed_m1: Sequence[Bar], price: float, forming_m1_open: float,
                     forming_opens: Sequence[float],
                     params: RejectionParams = RejectionParams()) -> Optional[ImpulseSignal]:
    """Trade with the higher timeframes after a short M1 pullback runs out (user's 390117817).

    - at least ``min_tf_agree`` forming M5..H4 candles on the trade side
    - ``pullback_bars`` closed M1 candles in a row against it, then one closed M1 back with it
    - the forming M1 is already moving our way (price beyond its open)

    Stop beyond the pullback extreme (pullback candles and the turn candle).
    """
    need = params.pullback_bars + 1
    if len(closed_m1) < need:
        return None
    pull = closed_m1[-need:-1]
    turn = closed_m1[-1]
    for side in (BUY, SELL):
        if sum(1 for o in forming_opens if (price - o) * side > 0) < params.min_tf_agree:
            continue
        if not all((b.close - b.open) * side < 0 for b in pull):
            continue
        if (turn.close - turn.open) * side <= 0 or (price - forming_m1_open) * side <= 0:
            continue
        legs = list(pull) + [turn]
        if side == BUY:
            sl = min(b.low for b in legs) - params.sl_buffer
        else:
            sl = max(b.high for b in legs) + params.sl_buffer
        move = abs(pull[-1].close - pull[0].open)
        agree = sum(1 for o in forming_opens if (price - o) * side > 0)
        return ImpulseSignal(side=side, price=price, sl=sl, push=move, range_width=0.0, tf_agree=agree)
    return None


def fade_exit(side: int, entry_price: float, bars_since_entry: Sequence[Bar], profit_usd: float,
              min_profit_usd: float, stall_bars: int = 2) -> Optional[str]:
    """Close a winner when momentum is gone: the last ``stall_bars`` closed M1 candles
    after entry made no new extreme in our favour, or all closed against us.

    Only fires while ``profit_usd`` is above ``min_profit_usd``; losers are left to the stop.
    """
    if profit_usd <= min_profit_usd or len(bars_since_entry) < stall_bars:
        return None
    tail = bars_since_entry[-stall_bars:]
    if all((b.close - b.open) * side < 0 for b in tail):
        return "fade_velas_en_contra"
    head = bars_since_entry[:-stall_bars]
    best = max((b.high for b in head), default=entry_price) if side == BUY else \
        min((b.low for b in head), default=entry_price)
    if side == BUY and all(b.high <= best for b in tail):
        return "fade_sin_maximo_nuevo"
    if side == SELL and all(b.low >= best for b in tail):
        return "fade_sin_minimo_nuevo"
    return None
