"""MetaTrader5 adapter for the swing engine (daily bars, long-only market orders).

Closed daily bars only (``copy_rates_from_pos`` from index 1). Swap is converted to
USD per lot-day with ``swap_long * point * tick_value / tick_size``; swap modes other
than points/deposit currency fail closed (reported as a prohibitive swap so the
domain skips the symbol).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from trading.application.swing_engine import AccountView, DailyBars, OrderResult, PositionView
from trading.domain.swing_donchian import Contract, swap_pct_year
from trading.domain.xbtfx import account_is_xbtfx

SWAP_MODE_DISABLED = 0
SWAP_MODE_POINTS = 1
SWAP_MODE_CURRENCY_DEPOSIT = 4
SWAP_UNKNOWN_PCT = -999.0
SYMBOL_TRADE_MODE_FULL = 4
RETCODE_UNSUPPORTED_FILLING = 10030


class Mt5SwingBroker:
    def __init__(self, mt5: Any, *, symbols: Optional[list[str]] = None, suffix: str = "!",
                 deviation: int = 30, xbtfx_markers: tuple[str, ...] = ("xbtfx",)):
        self.mt5 = mt5
        self._symbols = symbols
        self._suffix = suffix
        self._deviation = int(deviation)
        self._markers = xbtfx_markers

    def account(self) -> Optional[AccountView]:
        a = self.mt5.account_info()
        if a is None:
            return None
        server = str(getattr(a, "server", "") or "")
        company = str(getattr(a, "company", "") or "")
        return AccountView(login=int(a.login), server=server, trade_mode=int(a.trade_mode),
                           equity=float(a.equity), is_xbtfx=account_is_xbtfx(server, company, self._markers))

    def universe(self) -> list[str]:
        if self._symbols:
            return list(self._symbols)
        out = []
        for s in self.mt5.symbols_get() or ():
            name = str(s.name)
            if name.endswith(self._suffix) and int(getattr(s, "trade_mode", 0)) == SYMBOL_TRADE_MODE_FULL:
                out.append(name)
        return sorted(out)

    def daily_bars(self, symbol: str, count: int) -> Optional[DailyBars]:
        self.mt5.symbol_select(symbol, True)
        rates = self.mt5.copy_rates_from_pos(symbol, self.mt5.TIMEFRAME_D1, 1, int(count))
        if rates is None or len(rates) == 0:
            return None
        days = [datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).date() for r in rates]
        return DailyBars(days=days, highs=[float(r["high"]) for r in rates],
                         lows=[float(r["low"]) for r in rates], closes=[float(r["close"]) for r in rates])

    def contract(self, symbol: str) -> Optional[Contract]:
        i = self.mt5.symbol_info(symbol)
        t = self.mt5.symbol_info_tick(symbol)
        if i is None or t is None or float(i.trade_tick_size) <= 0 or float(t.bid) <= 0:
            return None
        usd_per_price_unit = float(i.trade_tick_value) / float(i.trade_tick_size)
        return Contract(contract_size=usd_per_price_unit, volume_min=float(i.volume_min),
                        volume_step=float(i.volume_step), volume_max=float(i.volume_max),
                        swap_pct_year=self._swap_pct(i, usd_per_price_unit, float(t.bid)))

    @staticmethod
    def _swap_pct(i: Any, usd_per_price_unit: float, bid: float) -> float:
        mode = int(i.swap_mode)
        if mode == SWAP_MODE_DISABLED:
            return 0.0
        if mode == SWAP_MODE_POINTS:
            usd = float(i.swap_long) * float(i.point) * usd_per_price_unit
        elif mode == SWAP_MODE_CURRENCY_DEPOSIT:
            usd = float(i.swap_long)
        else:
            return SWAP_UNKNOWN_PCT
        return swap_pct_year(swap_usd_per_lot_day=usd, notional_per_lot=bid * usd_per_price_unit)

    def positions(self, magic: int) -> list[PositionView]:
        rows = self.mt5.positions_get() or ()
        return [PositionView(ticket=int(p.ticket), symbol=str(p.symbol), volume=float(p.volume),
                             price_open=float(p.price_open), profit=float(p.profit))
                for p in rows if int(p.magic) == int(magic)]

    def magic_active(self, magic: int, hours: float = 2.0) -> bool:
        """True if ``magic`` has open positions or deals in the last ``hours`` (shared-account guard)."""
        if any(int(p.magic) == int(magic) for p in (self.mt5.positions_get() or ())):
            return True
        now = datetime.now(timezone.utc)
        deals = self.mt5.history_deals_get(now - timedelta(hours=hours), now + timedelta(days=1)) or ()
        return any(int(d.magic) == int(magic) for d in deals)

    def buy(self, symbol: str, lots: float, sl: float, magic: int, comment: str) -> OrderResult:
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            return OrderResult(ok=False, retcode=-1, comment="sin_tick")
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(lots),
               "type": self.mt5.ORDER_TYPE_BUY, "price": float(tick.ask), "sl": float(sl), "tp": 0.0,
               "deviation": self._deviation, "magic": int(magic), "comment": comment[:31],
               "type_time": self.mt5.ORDER_TIME_GTC}
        return self._send(symbol, req)

    def close(self, position: PositionView, magic: int, comment: str) -> OrderResult:
        tick = self.mt5.symbol_info_tick(position.symbol)
        if tick is None:
            return OrderResult(ok=False, retcode=-1, comment="sin_tick")
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": position.symbol, "volume": float(position.volume),
               "type": self.mt5.ORDER_TYPE_SELL, "position": int(position.ticket), "price": float(tick.bid),
               "deviation": self._deviation, "magic": int(magic), "comment": comment[:31],
               "type_time": self.mt5.ORDER_TIME_GTC}
        return self._send(position.symbol, req)

    def _send(self, symbol: str, req: dict) -> OrderResult:
        m = self.mt5
        result = None
        for fill in (m.ORDER_FILLING_IOC, m.ORDER_FILLING_FOK, getattr(m, "ORDER_FILLING_RETURN", m.ORDER_FILLING_IOC)):
            req["type_filling"] = fill
            try:
                result = m.order_send(req)
            except Exception:
                logging.exception("[%s] swing order_send raised", symbol)
                return OrderResult(ok=False, retcode=-1, comment="exception")
            if result is None:
                return OrderResult(ok=False, retcode=-1, comment=str(m.last_error()))
            if int(getattr(result, "retcode", -1)) != RETCODE_UNSUPPORTED_FILLING:
                break
        rc = int(getattr(result, "retcode", -1))
        return OrderResult(ok=rc == int(m.TRADE_RETCODE_DONE), retcode=rc, price=float(getattr(result, "price", 0.0) or 0.0),
                           ticket=int(getattr(result, "order", 0) or 0), comment=str(getattr(result, "comment", "")))
