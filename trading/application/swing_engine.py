"""Once-a-day swing cycle: Donchian exits, then entries, through a broker port.

Rules live in trading.domain.swing_donchian. This module only wires the broker,
the gates and the JSON log. Exits always run (manage-only); entries need
``send_orders`` and no block reason. A dry run logs the same plan without orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional, Protocol, Sequence

from trading.domain.swing_donchian import Contract, Levels, SwingParams, drawdown_halt, levels, plan_day

LogFn = Callable[[dict], None]


@dataclass(frozen=True)
class AccountView:
    login: int
    server: str
    trade_mode: int
    equity: float
    is_xbtfx: bool


@dataclass(frozen=True)
class DailyBars:
    days: Sequence[date]
    highs: Sequence[float]
    lows: Sequence[float]
    closes: Sequence[float]


@dataclass(frozen=True)
class PositionView:
    ticket: int
    symbol: str
    volume: float
    price_open: float
    profit: float


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    retcode: int
    price: float = 0.0
    ticket: int = 0
    comment: str = ""


class SwingBroker(Protocol):
    def account(self) -> Optional[AccountView]: ...
    def universe(self) -> list[str]: ...
    def daily_bars(self, symbol: str, count: int) -> Optional[DailyBars]: ...
    def contract(self, symbol: str) -> Optional[Contract]: ...
    def positions(self, magic: int) -> list[PositionView]: ...
    def buy(self, symbol: str, lots: float, sl: float, magic: int, comment: str) -> OrderResult: ...
    def close(self, position: PositionView, magic: int, comment: str) -> OrderResult: ...


def run_swing_day(
    broker: SwingBroker,
    *,
    today: date,
    params: SwingParams,
    magic: int,
    send_orders: bool,
    block_reason: str,
    equity_peak: float,
    log: LogFn,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Run one daily decision. Returns a summary with the new equity peak and halt flag."""
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    base = {"engine": "swing", "ts": ts, "day": today.isoformat(), "magic": magic}
    acct = broker.account()
    if acct is None:
        log(base | {"event": "SWING_ABORT", "reason": "sin_cuenta"})
        return {"ran": False, "reason": "sin_cuenta", "equity_peak": equity_peak, "halt": False}
    peak = max(float(equity_peak or 0.0), acct.equity)
    halt = drawdown_halt(equity=acct.equity, peak=peak, params=params)
    if halt and not block_reason:
        block_reason = "drawdown_halt"

    expected_last = today - timedelta(days=1)
    lv_by: dict[str, Optional[Levels]] = {}
    contracts: dict[str, Contract] = {}
    stale: list[str] = []
    for sym in broker.universe():
        bars = broker.daily_bars(sym, params.bars_needed + 5)
        if bars is None or not bars.days or bars.days[-1] != expected_last:
            lv_by[sym] = None
            stale.append(sym)
            continue
        lv_by[sym] = levels(bars.highs, bars.lows, bars.closes, params)
        c = broker.contract(sym)
        if c is not None:
            contracts[sym] = c

    open_pos = broker.positions(magic)
    open_syms = sorted({p.symbol for p in open_pos})
    plan = plan_day(levels_by_symbol=lv_by, open_symbols=open_syms, contracts=contracts,
                    equity=acct.equity, params=params)
    log(base | {"event": "SWING_PLAN", "equity": round(acct.equity, 2), "equity_peak": round(peak, 2),
                "send_orders": send_orders, "block_reason": block_reason, "open": open_syms,
                "exits": [list(x) for x in plan.exits], "entries": [e.symbol for e in plan.entries],
                "skips": [[s.symbol, s.reason] for s in plan.skips], "stale_bars": stale,
                "universe": len(lv_by)})

    closed = 0
    for sym, why in plan.exits:
        lv = lv_by[sym]
        for p in (x for x in open_pos if x.symbol == sym):
            ev = base | {"symbol": sym, "ticket": p.ticket, "why_closed": why, "volume": p.volume,
                         "price_open": p.price_open, "profit": p.profit,
                         "close_ref": lv.close if lv else None, "exit_low": lv.exit_low if lv else None,
                         "sma200": lv.sma if lv else None}
            if not send_orders:
                log(ev | {"event": "SWING_DRY_CLOSE"})
                continue
            r = broker.close(p, magic, f"swing_{why}"[:31])
            log(ev | {"event": "SWING_CLOSE" if r.ok else "SWING_CLOSE_FAIL", "retcode": r.retcode,
                      "fill": r.price, "broker_comment": r.comment})
            closed += int(r.ok)

    opened = 0
    for e in plan.entries:
        ev = base | {"symbol": e.symbol, "side": "BUY", "lots": e.lots, "entry_ref": e.entry_ref,
                     "exit_low": e.exit_low, "disaster_sl": e.disaster_sl, "risk_usd": round(e.risk_usd, 2),
                     "momentum_90d": round(e.momentum, 4), "why_open": "donchian_20_sma200"}
        if block_reason:
            log(ev | {"event": "SWING_SKIP", "reason": block_reason})
            continue
        if not send_orders:
            log(ev | {"event": "SWING_DRY_OPEN"})
            continue
        r = broker.buy(e.symbol, e.lots, e.disaster_sl, magic, "swing_don20")
        log(ev | {"event": "SWING_OPEN" if r.ok else "SWING_OPEN_FAIL", "retcode": r.retcode,
                  "fill": r.price, "ticket": r.ticket, "broker_comment": r.comment})
        opened += int(r.ok)

    return {"ran": True, "equity_peak": peak, "halt": halt, "exits": len(plan.exits), "entries": len(plan.entries),
            "closed": closed, "opened": opened, "block_reason": block_reason,
            "stale": len(stale), "universe": len(lv_by)}
