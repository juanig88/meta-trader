"""Swing daily cycle with a fake broker: gates, dry run, exits before entries, stale bars."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading.application.swing_engine import AccountView, DailyBars, OrderResult, PositionView, run_swing_day
from trading.domain.swing_donchian import Contract, SwingParams

TODAY = date(2026, 10, 7)
C = Contract(contract_size=1.0, volume_min=0.01, volume_step=0.01, volume_max=100.0, swap_pct_year=-21.0)


def bars(last_close: float, n: int = 230, base: float = 100.0, last_day: date = TODAY - timedelta(days=1)) -> DailyBars:
    closes = [base] * (n - 1) + [last_close]
    days = [last_day - timedelta(days=n - 1 - i) for i in range(n)]
    return DailyBars(days=days, highs=[c * 1.01 for c in closes], lows=[c * 0.99 for c in closes], closes=closes)


class FakeBroker:
    def __init__(self, series: dict, open_pos=(), equity=10_000.0, fail_buy=False):
        self.series = series
        self.open_pos = list(open_pos)
        self.equity = equity
        self.fail_buy = fail_buy
        self.calls: list[tuple] = []

    def account(self):
        return AccountView(login=1, server="XBTFX-Demo", trade_mode=0, equity=self.equity, is_xbtfx=True)

    def universe(self):
        return list(self.series)

    def daily_bars(self, symbol, count):
        return self.series[symbol]

    def contract(self, symbol):
        return C

    def positions(self, magic):
        return list(self.open_pos)

    def buy(self, symbol, lots, sl, magic, comment):
        self.calls.append(("buy", symbol, lots, sl, magic))
        return OrderResult(ok=not self.fail_buy, retcode=10030 if self.fail_buy else 10009, price=1.0, ticket=7)

    def close(self, position, magic, comment):
        self.calls.append(("close", position.symbol, position.ticket))
        return OrderResult(ok=True, retcode=10009, price=1.0)


def run(broker, **kw):
    events: list[dict] = []
    args = dict(today=TODAY, params=SwingParams(), magic=260501, send_orders=True, block_reason="",
                equity_peak=0.0, log=events.append)
    args.update(kw)
    out = run_swing_day(broker, **args)
    return out, events


def kinds(events):
    return [e["event"] for e in events]


def test_breakout_opens_with_disaster_stop():
    b = FakeBroker({"SOLUSD!": bars(120.0)})
    out, ev = run(b)
    assert b.calls and b.calls[0][0] == "buy" and b.calls[0][1] == "SOLUSD!"
    assert b.calls[0][3] < 99.0  # disaster SL below the 10d low
    assert kinds(ev) == ["SWING_PLAN", "SWING_OPEN"]
    assert out["opened"] == 1


def test_exit_runs_before_entry_and_sends_close():
    pos = PositionView(ticket=11, symbol="ETHUSD!", volume=0.5, price_open=120.0, profit=-3.0)
    b = FakeBroker({"ETHUSD!": bars(90.0), "SOLUSD!": bars(120.0)}, open_pos=[pos])
    out, ev = run(b)
    assert [c[0] for c in b.calls] == ["close", "buy"]
    assert ev[1]["event"] == "SWING_CLOSE" and ev[1]["why_closed"] == "bajo_minimo_10d"


@pytest.mark.parametrize("name,kw,expected_events,expected_calls", [
    ("dry run logs plan, no orders", dict(send_orders=False), ["SWING_PLAN", "SWING_DRY_OPEN"], 0),
    ("kill switch blocks entries", dict(block_reason="kill_switch_halt"), ["SWING_PLAN", "SWING_SKIP"], 0),
    ("shared account blocks entries", dict(block_reason="cuenta_compartida_con_rotacion"), ["SWING_PLAN", "SWING_SKIP"], 0),
])
def test_gates(name, kw, expected_events, expected_calls):
    b = FakeBroker({"SOLUSD!": bars(120.0)})
    _, ev = run(b, **kw)
    assert kinds(ev) == expected_events, name
    assert len(b.calls) == expected_calls, name


def test_blocked_entries_still_manage_exits():
    pos = PositionView(ticket=11, symbol="ETHUSD!", volume=0.5, price_open=120.0, profit=-3.0)
    b = FakeBroker({"ETHUSD!": bars(90.0)}, open_pos=[pos])
    run(b, block_reason="kill_switch_halt")
    assert b.calls == [("close", "ETHUSD!", 11)]


def test_drawdown_halt_blocks_entries_and_reports():
    b = FakeBroker({"SOLUSD!": bars(120.0)}, equity=6_000.0)
    out, ev = run(b, equity_peak=10_000.0)
    assert out["halt"] is True and out["block_reason"] == "drawdown_halt"
    assert b.calls == []
    assert out["equity_peak"] == 10_000.0


def test_stale_daily_bar_is_not_traded():
    b = FakeBroker({"SOLUSD!": bars(120.0, last_day=TODAY - timedelta(days=3))})
    _, ev = run(b)
    assert b.calls == []
    assert ev[0]["stale_bars"] == ["SOLUSD!"]


def test_failed_order_is_logged_not_counted():
    b = FakeBroker({"SOLUSD!": bars(120.0)}, fail_buy=True)
    out, ev = run(b)
    assert ev[-1]["event"] == "SWING_OPEN_FAIL" and out["opened"] == 0


def test_no_account_aborts():
    b = FakeBroker({})
    b.account = lambda: None
    out, ev = run(b)
    assert out["ran"] is False and ev[0]["event"] == "SWING_ABORT"
