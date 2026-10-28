"""Mt5SwingBroker against a fake MetaTrader5 module."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from trading.application.swing_engine import PositionView
from trading.infrastructure.mt5_swing_broker import SWAP_UNKNOWN_PCT, Mt5SwingBroker


class FakeMt5:
    TIMEFRAME_D1 = 16408
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, info=None, positions=(), deals=(), retcodes=(10009,)):
        self.info = info or {}
        self._positions = list(positions)
        self._deals = list(deals)
        self._retcodes = list(retcodes)
        self.sent: list[dict] = []
        self.rates_args = None

    def symbol_info(self, s):
        return self.info.get(s)

    def symbol_info_tick(self, s):
        i = self.info.get(s)
        return NS(bid=i.bid, ask=i.bid * 1.001) if i else None

    def symbol_select(self, s, on):
        return True

    def copy_rates_from_pos(self, s, tf, start, count):
        self.rates_args = (s, tf, start, count)
        return [{"time": 1791244800, "high": 2.0, "low": 1.0, "close": 1.5}]

    def positions_get(self):
        return self._positions

    def history_deals_get(self, a, b):
        self.deals_window = (a, b)
        return self._deals

    def order_send(self, req):
        self.sent.append(dict(req))
        rc = self._retcodes.pop(0) if self._retcodes else 10009
        return NS(retcode=rc, price=req["price"], order=99, comment="")

    def last_error(self):
        return (0, "")


def info(bid, point, swap_long, mode=1):
    return NS(bid=bid, point=point, swap_long=swap_long, swap_mode=mode, trade_tick_value=point,
              trade_tick_size=point, volume_min=0.01, volume_step=0.01, volume_max=1000.0)


@pytest.mark.parametrize("sym,i,expected", [
    ("BTCUSD!", info(83353.5, 0.001, -46029.79), -20.16),   # MT5 read 2026-10-07
    ("SOLUSD!", info(116.26, 0.01, -6.43), -20.19),
    ("OFF!", info(10.0, 0.01, -5.0, mode=0), 0.0),
    ("WEIRD!", info(10.0, 0.01, -5.0, mode=2), SWAP_UNKNOWN_PCT),
])
def test_contract_swap_pct(sym, i, expected):
    c = Mt5SwingBroker(FakeMt5(info={sym: i})).contract(sym)
    assert c.swap_pct_year == pytest.approx(expected, abs=0.05)
    assert c.contract_size == pytest.approx(1.0)


def test_daily_bars_skip_forming_bar():
    m = FakeMt5()
    bars = Mt5SwingBroker(m).daily_bars("BTCUSD!", 210)
    assert m.rates_args[2] == 1
    assert bars.days[-1].isoformat() == "2026-10-06"


def test_positions_and_magic_guard():
    pos = [NS(ticket=1, symbol="BTCUSD!", volume=0.1, price_open=1.0, profit=0.0, magic=260501),
           NS(ticket=2, symbol="ETHUSD!", volume=0.1, price_open=1.0, profit=0.0, magic=260414)]
    b = Mt5SwingBroker(FakeMt5(positions=pos))
    assert [p.ticket for p in b.positions(260501)] == [1]
    assert b.magic_active(260414) is True
    assert Mt5SwingBroker(FakeMt5(deals=[NS(magic=260414)])).magic_active(260414) is True
    assert Mt5SwingBroker(FakeMt5()).magic_active(260414) is False


def test_magic_guard_window_is_hours():
    m = FakeMt5()
    Mt5SwingBroker(m).magic_active(260414, hours=2.0)
    a, b = m.deals_window
    from datetime import datetime, timezone
    assert abs((datetime.now(timezone.utc) - a).total_seconds() - 7200) < 60


def test_buy_retries_unsupported_filling_and_sets_sl():
    m = FakeMt5(info={"BTCUSD!": info(100.0, 0.01, 0.0)}, retcodes=(10030, 10009))
    r = Mt5SwingBroker(m).buy("BTCUSD!", 0.05, 90.0, 260501, "swing_don20")
    assert r.ok and len(m.sent) == 2
    assert [s["type_filling"] for s in m.sent] == [1, 0]
    assert m.sent[-1]["sl"] == 90.0 and m.sent[-1]["magic"] == 260501 and m.sent[-1]["type"] == 0


def test_close_sells_position_ticket():
    m = FakeMt5(info={"BTCUSD!": info(100.0, 0.01, 0.0)})
    r = Mt5SwingBroker(m).close(PositionView(7, "BTCUSD!", 0.05, 95.0, 1.0), 260501, "swing_bajo_sma200")
    assert r.ok and m.sent[0]["position"] == 7 and m.sent[0]["type"] == 1


def test_rejected_order_not_ok():
    m = FakeMt5(info={"BTCUSD!": info(100.0, 0.01, 0.0)}, retcodes=(10019,))
    assert Mt5SwingBroker(m).buy("BTCUSD!", 0.05, 90.0, 260501, "x").ok is False
