"""TP chico, cell C1 of PREREG_TP_CHICO.md: forward test on the Exness demo. Pure: no MT5, no clock.

- one position at a time; at each new UTC hour while flat, take the side of yesterday's daily EMA 20
  (price below -> sell, above -> buy)
- 0.01 lot, TP 100 and SL 1000 of price from the fill (+1 / -10 USD)
- after 9 wins in a row, skip one entry and start a new cycle
- stop for good at 100 trades or -30 USD net

It failed the train (2018-2025: -0.14 USD per trade); October 2026 on Exness gave +0.21. The demo
run says which one the next 100 trades look like. Runbook: trading/docs/TP_CHICO.md
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

BUY = 1
SELL = -1
CYCLE = 9
TP_PRICE = 100.0
SL_PRICE = 1000.0
MAX_TRADES = 100
MAX_LOSS_USD = 30.0

OPEN = "abrir"
SKIP_CYCLE = "saltear_ciclo"
DONE = "terminado"


@dataclass
class CycleState:
    streak: int = 0
    trades: int = 0
    wins: int = 0
    skips: int = 0
    net_usd: float = 0.0
    finished: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "CycleState":
        return cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})


def ema_last(closes: Sequence[float], span: int = 20) -> Optional[float]:
    """EMA of closed daily bars, oldest first (pandas ``ewm(adjust=False)``)."""
    if len(closes) < span:
        return None
    alpha = 2.0 / (span + 1)
    e = float(closes[0])
    for c in closes[1:]:
        e = alpha * float(c) + (1 - alpha) * e
    return e


def trend_side(price: float, ema_prev: Optional[float]) -> int:
    if ema_prev is None or ema_prev <= 0:
        return 0
    return SELL if price < ema_prev else BUY


def decide(state: CycleState) -> str:
    """What to do at a new hour while flat. A skip resets the streak (mutates ``state``)."""
    if state.finished:
        return DONE
    if state.streak >= CYCLE:
        state.streak = 0
        state.skips += 1
        return SKIP_CYCLE
    return OPEN


def levels(side: int, bid: float, ask: float) -> tuple[float, float]:
    """(sl, tp) measured from the fill: ask for a buy, bid for a sell."""
    if side == BUY:
        return ask - SL_PRICE, ask + TP_PRICE
    return bid + SL_PRICE, bid - TP_PRICE


def record_close(state: CycleState, net_usd: float) -> None:
    """Count a closed trade (broker net) and mark the run finished when a stop rule is hit."""
    win = net_usd > 0
    state.trades += 1
    state.wins += int(win)
    state.net_usd = round(state.net_usd + net_usd, 2)
    state.streak = state.streak + 1 if win else 0
    if state.trades >= MAX_TRADES:
        state.finished = f"{MAX_TRADES}_operaciones"
    elif state.net_usd <= -MAX_LOSS_USD:
        state.finished = f"perdida_{int(MAX_LOSS_USD)}_usd"
