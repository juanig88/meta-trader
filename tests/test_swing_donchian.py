"""Daily Donchian 20/10 + SMA200 swing rules (pure domain)."""

from __future__ import annotations

import pytest

from trading.domain.swing_donchian import (
    EXIT_LOW,
    EXIT_REGIME,
    SKIP_CAP,
    SKIP_MIN_LOT,
    SKIP_STOP,
    SKIP_SWAP,
    Contract,
    Levels,
    SwingParams,
    drawdown_halt,
    entry_signal,
    exit_reason,
    levels,
    plan_day,
    size_lots,
    swap_pct_year,
)

P = SwingParams()
C = Contract(contract_size=1.0, volume_min=0.01, volume_step=0.01, volume_max=100.0, swap_pct_year=-21.0)


def lv(close=110.0, sma=100.0, hi=105.0, lo=95.0, mom=0.2):
    return Levels(close=close, sma=sma, entry_high=hi, exit_low=lo, momentum=mom)


def flat_then(last_close: float, n: int = 220, base: float = 100.0):
    closes = [base] * (n - 1) + [last_close]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    return highs, lows, closes


def test_levels_uses_prior_bars_only():
    highs, lows, closes = flat_then(120.0)
    out = levels(highs, lows, closes, P)
    assert out is not None
    assert out.entry_high == pytest.approx(101.0)  # today's high (121.2) excluded
    assert out.exit_low == pytest.approx(99.0)
    assert out.sma == pytest.approx((100.0 * 199 + 120.0) / 200)
    assert out.momentum == pytest.approx(0.2)


@pytest.mark.parametrize("name,closes_len,bad", [
    ("short history", 150, None),
    ("zero close", 220, 0.0),
    ("nan close", 220, float("nan")),
])
def test_levels_rejects_broken_history(name, closes_len, bad):
    highs, lows, closes = flat_then(100.0, n=closes_len)
    if bad is not None:
        closes[-5] = bad
    assert levels(highs, lows, closes, P) is None, name


@pytest.mark.parametrize("name,level,expected", [
    ("breakout above regime", lv(), True),
    ("not above 20d high", lv(close=104.0), False),
    ("below sma200", lv(close=110.0, sma=115.0), False),
    ("exactly at high", lv(close=105.0), False),
])
def test_entry_signal(name, level, expected):
    assert entry_signal(level) is expected, name


@pytest.mark.parametrize("name,level,expected", [
    ("hold", lv(close=100.0, sma=90.0), None),
    ("below 10d low", lv(close=94.0, sma=90.0), EXIT_LOW),
    ("below sma200 above low", lv(close=99.0, sma=100.0, lo=95.0), EXIT_REGIME),
    ("both: low wins", lv(close=90.0, sma=100.0, lo=95.0), EXIT_LOW),
])
def test_exit_reason(name, level, expected):
    assert exit_reason(level) == expected, name


@pytest.mark.parametrize("name,kw,expected_lots,expected_reason", [
    ("0.5% of 10k over 5 usd", dict(equity=10_000, risk_frac=0.005, entry=105, stop=100, contract=C), 10.0, ""),
    ("floors, never rounds up", dict(equity=10_000, risk_frac=0.005, entry=103, stop=100, contract=C), 16.66, ""),
    ("below min lot is skipped", dict(equity=100, risk_frac=0.005, entry=200, stop=100, contract=C), 0.0, SKIP_MIN_LOT),
    ("stop above entry", dict(equity=10_000, risk_frac=0.005, entry=100, stop=101, contract=C), 0.0, SKIP_STOP),
    ("zero equity", dict(equity=0, risk_frac=0.005, entry=105, stop=100, contract=C), 0.0, SKIP_STOP),
    ("capped at volume_max", dict(equity=1e9, risk_frac=0.005, entry=105, stop=100, contract=C), 100.0, ""),
])
def test_size_lots(name, kw, expected_lots, expected_reason):
    lots, why = size_lots(**kw)
    assert lots == pytest.approx(expected_lots), name
    assert why == expected_reason, name


def test_plan_exits_first_and_fills_freed_slot_by_momentum():
    params = SwingParams(max_positions=2)
    levels_by = {
        "OLD": lv(close=94.0, sma=90.0, lo=95.0),     # exits
        "KEEP": lv(close=100.0, sma=90.0, lo=95.0),   # holds
        "A": lv(mom=0.10),
        "B": lv(mom=0.50),
    }
    contracts = {k: C for k in levels_by}
    plan = plan_day(levels_by_symbol=levels_by, open_symbols=["OLD", "KEEP"], contracts=contracts,
                    equity=10_000, params=params)
    assert plan.exits == (("OLD", EXIT_LOW),)
    assert [e.symbol for e in plan.entries] == ["B"]
    assert ("A", SKIP_CAP) in {(s.symbol, s.reason) for s in plan.skips}


def test_plan_entry_carries_disaster_stop_and_risk():
    plan = plan_day(levels_by_symbol={"X": lv(close=110.0, lo=100.0)}, open_symbols=[],
                    contracts={"X": C}, equity=10_000, params=P)
    (e,) = plan.entries
    assert e.disaster_sl == pytest.approx(90.0)
    assert e.risk_usd == pytest.approx(e.lots * 10.0)
    assert e.risk_usd <= 0.005 * 10_000 + 1e-9


def test_plan_skips_expensive_swap_and_ignores_missing_contract():
    shib = Contract(contract_size=1.0, volume_min=0.01, volume_step=0.01, volume_max=100.0, swap_pct_year=-88.6)
    plan = plan_day(levels_by_symbol={"SHB": lv(), "NOSPEC": lv()}, open_symbols=[],
                    contracts={"SHB": shib}, equity=10_000, params=P)
    assert plan.entries == ()
    assert plan.skips[0].reason == SKIP_SWAP


def test_plan_does_not_reenter_open_symbol_or_exit_without_levels():
    plan = plan_day(levels_by_symbol={"X": lv(), "GAP": None}, open_symbols=["X", "GAP"],
                    contracts={"X": C}, equity=10_000, params=P)
    assert plan.entries == () and plan.exits == ()


@pytest.mark.parametrize("equity,peak,expected", [(6_500, 10_000, True), (6_600, 10_000, False), (100, 0, False)])
def test_drawdown_halt(equity, peak, expected):
    assert drawdown_halt(equity=equity, peak=peak, params=P) is expected


@pytest.mark.parametrize("usd,notional,expected", [
    (-46.03, 83445.0, -20.1),   # BTCUSD! read 2026-10-06
    (0.0, 100.0, 0.0),
    (-1.0, 0.0, 0.0),
])
def test_swap_pct_year(usd, notional, expected):
    assert swap_pct_year(swap_usd_per_lot_day=usd, notional_per_lot=notional) == pytest.approx(expected, abs=0.1)
