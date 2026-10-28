"""Golden test: pins what open_trade sends for the rotation path (lot, SL/TP, comment, magic)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402
import telegram_notify  # noqa: E402

MAGIC = 260414

SYMBOLS = {
    # cheap alt: $0.50 does not fit inside 5% at volume_min → ±5% stop, lot scales
    "ADAUSD!": dict(bid=0.60000, ask=0.60010, point=1e-5, digits=5, tick_size=1e-5, tick_value=1e-5,
                    volume_min=0.01, volume_step=0.01, volume_max=10000.0),
    # expensive CFD: 5% at volume_min is > $0.50 → stop shrinks to the money stop
    "BTCUSD!": dict(bid=62000.000, ask=62001.000, point=0.001, digits=3, tick_size=0.001, tick_value=0.001,
                    volume_min=0.01, volume_step=0.01, volume_max=1000.0),
    # spread hole > 10% of risk → spread_caro
    "WIDUSD!": dict(bid=1.0000, ask=1.0200, point=1e-4, digits=4, tick_size=1e-4, tick_value=1e-4,
                    volume_min=0.01, volume_step=0.01, volume_max=10000.0),
}


def _cfg() -> bot_mt5.Config:
    return bot_mt5.Config(
        symbols=tuple(SYMBOLS),
        magic=MAGIC,
        max_risk_usd=0.50,
        rotation_max_spread_usd_frac=0.10,
        portfolio_enabled=True,
        max_open_trades=60,
        sl_money=-0.50,
        tp_money=0.50,
        trading_window_enabled=False,
    )


def _run(monkeypatch, symbol: str, side: str, *, halt: bool = False, risk_usd: float = 0.50):
    s = SYMBOLS[symbol]
    tick = SimpleNamespace(bid=s["bid"], ask=s["ask"], time=0)
    info = SimpleNamespace(
        point=s["point"], digits=s["digits"], trade_tick_size=s["tick_size"], trade_tick_value=s["tick_value"],
        volume_min=s["volume_min"], volume_step=s["volume_step"], volume_max=s["volume_max"],
        trade_stops_level=0, trade_freeze_level=0, trade_mode=4, visible=True,
    )
    sent: list[dict] = []
    events: list[dict] = []

    def order_send(req):
        sent.append(dict(req))
        return SimpleNamespace(retcode=bot_mt5.mt5.TRADE_RETCODE_DONE, deal=111, order=222, comment="done", request_id=1)

    m = bot_mt5.mt5
    monkeypatch.setattr(m, "symbol_info_tick", lambda sym: tick)
    monkeypatch.setattr(m, "symbol_info", lambda sym: info)
    monkeypatch.setattr(m, "terminal_info", lambda: SimpleNamespace(trade_allowed=True))
    monkeypatch.setattr(m, "account_info", lambda: SimpleNamespace(balance=1000.0, equity=1000.0, margin_free=1e9, currency="UST"))
    monkeypatch.setattr(m, "order_send", order_send)
    monkeypatch.setattr(m, "copy_rates_from_pos", lambda *a, **k: None)
    monkeypatch.setattr(m, "order_calc_margin", lambda *a, **k: 0.0)
    monkeypatch.setattr(bot_mt5, "_kill_switch_halt_active", lambda cfg: halt)
    monkeypatch.setattr(bot_mt5, "_ensure_symbol", lambda sym: True)
    monkeypatch.setattr(bot_mt5, "_can_open_additional_position", lambda *a, **k: (True, ""))
    monkeypatch.setattr(bot_mt5, "_count_managed_open_positions", lambda cfg: 0)
    monkeypatch.setattr(bot_mt5, "_is_spread_ok", lambda *a, **k: (True, (s["ask"] - s["bid"]) / s["point"], None))
    monkeypatch.setattr(bot_mt5, "_currency_exposure_blocks_open", lambda *a, **k: False)
    monkeypatch.setattr(bot_mt5, "_position_id_from_deal_ticket", lambda t: 222)
    monkeypatch.setattr(bot_mt5, "_log_event", lambda p: events.append(dict(p)))
    monkeypatch.setattr(telegram_notify, "telegram_configured", lambda: False)
    monkeypatch.setitem(
        bot_mt5._ROTATION_OVERRIDE,
        symbol,
        {"lot": s["volume_min"], "sl_money": -abs(risk_usd), "tp_money": abs(risk_usd), "sl_points": 0.0,
         "tp_points": 0.0, "use_risk_sizing": False, "anti_chase_enabled": False, "cooldown_minutes": 0,
         "max_trades_per_hour_per_symbol": 0},
    )
    ok = bot_mt5.open_trade(_cfg(), symbol, side, audit={"why_open": "consenso_4_5", "votes": "htf:BUY"})
    return ok, sent, events


BASE = {"action": 1, "deviation": 20, "magic": MAGIC, "comment": "rot_4_5", "type_time": 0}

GOLDEN = [
    # name, symbol, side, risk, order request (None = rejected), event fields
    (
        "ada_buy_5pct_stop", "ADAUSD!", "BUY", 0.50,
        dict(BASE, symbol="ADAUSD!", volume=16.66, type=0, price=0.6001, sl=0.5701, tp=0.6301),
        dict(event="TRADE_OPEN", lot=16.66, sl=0.5701, tp=0.6301, stop_dist=0.030005,
             spread_usd=0.001666, spread_vs_risk=0.003332, engine="rotation", why_open="consenso_4_5", magic=MAGIC),
    ),
    (
        "ada_sell_5pct_stop", "ADAUSD!", "SELL", 0.50,
        dict(BASE, symbol="ADAUSD!", volume=16.66, type=1, price=0.6, sl=0.63, tp=0.57),
        dict(event="TRADE_OPEN", lot=16.66, sl=0.63, tp=0.57, stop_dist=0.03,
             spread_usd=0.001666, spread_vs_risk=0.003332, engine="rotation", why_open="consenso_4_5", magic=MAGIC),
    ),
    (
        "btc_buy_money_stop", "BTCUSD!", "BUY", 0.50,
        dict(BASE, symbol="BTCUSD!", volume=0.01, type=0, price=62001.0, sl=61951.0, tp=62051.0),
        dict(event="TRADE_OPEN", lot=0.01, sl=61951.0, tp=62051.0, stop_dist=50.0,
             spread_usd=0.01, spread_vs_risk=0.02, engine="rotation", why_open="consenso_4_5", magic=MAGIC),
    ),
    (
        "btc_sell_risk_030_keeps_050_distance", "BTCUSD!", "SELL", 0.30,
        dict(BASE, symbol="BTCUSD!", volume=0.01, type=1, price=62000.0, sl=62050.0, tp=61950.0),
        dict(event="TRADE_OPEN", lot=0.01, sl=62050.0, tp=61950.0, stop_dist=50.0,
             spread_usd=0.01, spread_vs_risk=0.033333, engine="rotation", why_open="consenso_4_5", magic=MAGIC),
    ),
    (
        "wide_spread_rejected", "WIDUSD!", "BUY", 0.50,
        None,
        dict(event="ROTATION_OPEN_REJECT", reason="spread_caro", lot=9.8, spread_usd=0.196, spread_vs_risk=0.392),
    ),
]


@pytest.mark.parametrize("name,symbol,side,risk,want_request,want_event", GOLDEN)
def test_open_trade_golden(monkeypatch, name, symbol, side, risk, want_request, want_event):
    ok, sent, events = _run(monkeypatch, symbol, side, risk_usd=risk)
    assert ok is (want_request is not None), name
    if want_request is None:
        assert sent == [], name
    else:
        assert len(sent) == 1, name
        assert {k: sent[0].get(k) for k in want_request} == pytest.approx(want_request), name
    assert len(events) == 1, name
    got = {k: events[0].get(k) for k in want_event}
    for k, v in want_event.items():
        if isinstance(v, float):
            assert got[k] == pytest.approx(v), (name, k)
        else:
            assert got[k] == v, (name, k)


def test_open_trade_kill_switch_rejects(monkeypatch):
    ok, sent, events = _run(monkeypatch, "ADAUSD!", "BUY", halt=True)
    assert ok is False
    assert sent == []
