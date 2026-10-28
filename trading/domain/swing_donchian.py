"""Daily Donchian 20/10 + SMA200 swing rules. Pure: no MetaTrader5, no files, no clock.

Long only. Decided once per day on CLOSED daily bars (oldest first, the last
element is the bar that just closed):

- exit when close < prior 10-day low or close < SMA200
- enter when close > prior 20-day high and close > SMA200
- lots = risk_frac * equity / (entry - prior 10-day low), floored to the
  broker step; below volume_min the entry is skipped, never rounded up
- more signals than free slots: highest 90-day return first

Same decisions as analyze_swing_portfolio.py (V3, 2026-10-07). Risk numbers are
risk-quant's: 0.5% per trade / 10 positions passed every pre-registered check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

EXIT_LOW = "bajo_minimo_10d"
EXIT_REGIME = "bajo_sma200"
SKIP_MIN_LOT = "bajo_volume_min"
SKIP_STOP = "stop_invalido"
SKIP_CAP = "tope_posiciones"
SKIP_SWAP = "swap_caro"


@dataclass(frozen=True)
class SwingParams:
    n_entry: int = 20
    n_exit: int = 10
    n_regime: int = 200
    n_momentum: int = 90
    risk_frac: float = 0.005
    max_positions: int = 10
    disaster_stop_mult: float = 2.0
    max_swap_pct_year: float = 30.0
    max_drawdown_halt: float = 0.35

    @property
    def bars_needed(self) -> int:
        return max(self.n_regime, self.n_entry + 1, self.n_exit + 1, self.n_momentum + 1)


@dataclass(frozen=True)
class Levels:
    close: float
    sma: float
    entry_high: float
    exit_low: float
    momentum: float


@dataclass(frozen=True)
class Contract:
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    swap_pct_year: float = 0.0


@dataclass(frozen=True)
class Entry:
    symbol: str
    lots: float
    entry_ref: float
    exit_low: float
    disaster_sl: float
    risk_usd: float
    momentum: float


@dataclass(frozen=True)
class Skip:
    symbol: str
    reason: str


@dataclass(frozen=True)
class DayPlan:
    exits: tuple[tuple[str, str], ...]
    entries: tuple[Entry, ...]
    skips: tuple[Skip, ...]


def levels(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
           params: SwingParams = SwingParams()) -> Optional[Levels]:
    """Levels at the last closed bar, or None when history is short or broken."""
    n = len(closes)
    if n < params.bars_needed or len(highs) != n or len(lows) != n:
        return None
    vals = [float(x) for x in closes[-params.n_regime:]]
    if any(not math.isfinite(v) or v <= 0 for v in vals):
        return None
    close = float(closes[-1])
    prior_h = [float(x) for x in highs[-params.n_entry - 1:-1]]
    prior_l = [float(x) for x in lows[-params.n_exit - 1:-1]]
    if any(not math.isfinite(v) for v in prior_h + prior_l):
        return None
    base = float(closes[-params.n_momentum - 1])
    return Levels(close=close, sma=sum(vals) / len(vals), entry_high=max(prior_h), exit_low=min(prior_l),
                  momentum=close / base - 1 if base > 0 else 0.0)


def exit_reason(lv: Levels) -> Optional[str]:
    if lv.close < lv.exit_low:
        return EXIT_LOW
    if lv.close < lv.sma:
        return EXIT_REGIME
    return None


def entry_signal(lv: Levels) -> bool:
    return lv.close > lv.entry_high and lv.close > lv.sma and lv.exit_low < lv.close


def size_lots(*, equity: float, risk_frac: float, entry: float, stop: float, contract: Contract) -> tuple[float, str]:
    """Floor to volume_step. Returns (0, reason) instead of rounding risk up."""
    dist = float(entry) - float(stop)
    if not (dist > 0 and equity > 0 and contract.contract_size > 0):
        return 0.0, SKIP_STOP
    raw = (float(risk_frac) * float(equity) / dist) / float(contract.contract_size)
    step = float(contract.volume_step) if contract.volume_step > 0 else float(contract.volume_min)
    lots = math.floor(raw / step + 1e-9) * step
    lots = min(lots, float(contract.volume_max)) if contract.volume_max > 0 else lots
    if lots + 1e-12 < float(contract.volume_min):
        return 0.0, SKIP_MIN_LOT
    return round(lots, 8), ""


def plan_day(*, levels_by_symbol: Mapping[str, Optional[Levels]], open_symbols: Sequence[str],
             contracts: Mapping[str, Contract], equity: float, params: SwingParams = SwingParams()) -> DayPlan:
    """Exits first, then entries into the slots that exits freed."""
    exits: list[tuple[str, str]] = []
    for sym in open_symbols:
        lv = levels_by_symbol.get(sym)
        if lv is None:
            continue
        why = exit_reason(lv)
        if why:
            exits.append((sym, why))
    still_open = set(open_symbols) - {s for s, _ in exits}
    skips: list[Skip] = []
    cands: list[tuple[str, Levels]] = []
    for sym, lv in levels_by_symbol.items():
        if sym in still_open or sym in {s for s, _ in exits} or lv is None or not entry_signal(lv):
            continue
        c = contracts.get(sym)
        if c is None:
            continue
        if abs(c.swap_pct_year) > params.max_swap_pct_year:
            skips.append(Skip(sym, SKIP_SWAP))
            continue
        cands.append((sym, lv))
    cands.sort(key=lambda x: -x[1].momentum)
    entries: list[Entry] = []
    slots = max(0, params.max_positions - len(still_open))
    for sym, lv in cands:
        if len(entries) >= slots:
            skips.append(Skip(sym, SKIP_CAP))
            continue
        lots, why = size_lots(equity=equity, risk_frac=params.risk_frac, entry=lv.close, stop=lv.exit_low,
                              contract=contracts[sym])
        if why:
            skips.append(Skip(sym, why))
            continue
        dist = lv.close - lv.exit_low
        entries.append(Entry(symbol=sym, lots=lots, entry_ref=lv.close, exit_low=lv.exit_low,
                             disaster_sl=max(lv.close - params.disaster_stop_mult * dist, 0.0),
                             risk_usd=lots * contracts[sym].contract_size * dist, momentum=lv.momentum))
    return DayPlan(exits=tuple(exits), entries=tuple(entries), skips=tuple(skips))


def drawdown_halt(*, equity: float, peak: float, params: SwingParams = SwingParams()) -> bool:
    """True when equity fell max_drawdown_halt below its high-water mark."""
    return peak > 0 and equity <= peak * (1.0 - params.max_drawdown_halt)


def swap_pct_year(*, swap_usd_per_lot_day: float, notional_per_lot: float) -> float:
    """Broker long swap as % of notional per year (negative = we pay)."""
    if notional_per_lot <= 0:
        return 0.0
    return float(swap_usd_per_lot_day) / float(notional_per_lot) * 365.0 * 100.0
